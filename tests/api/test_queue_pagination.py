"""Pagination, and proof that the queue no longer scales its query count.

`list_queue` used to call `_latest_run` and `_latest_score` inside the loop —
two extra round trips per card. At 138 applications that is 277 queries for one
page. The fix is a pair of lateral joins, and the test for it counts statements
rather than trusting the code to look right.
"""

import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, get_db
from jobpilot_shared.db.models import Application, Company, Job, Score, TailoringRun
from sqlalchemy import event


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def count_selects(db_engine):
    """Count SELECT statements issued against the database."""
    counter = {"n": 0}

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(db_engine, "before_cursor_execute", before)
    yield counter
    event.remove(db_engine, "before_cursor_execute", before)


def seed(db, how_many: int, *, location_kind: str = "remote") -> None:
    company = db.query(Company).filter_by(normalized_name="acme").one_or_none()
    if company is None:
        company = Company(name="Acme Corp", normalized_name="acme", discovered_via="seed")
        db.add(company)
        db.flush()
    company_id = company.id

    existing = db.query(Job).count()
    for index in range(existing, existing + how_many):
        job = Job(
            company_id=company_id,
            source="greenhouse",
            external_id=f"gh-{index}",
            title=f"Engineer {index}",
            location="Remote",
            description="Python.",
            apply_url="https://example.invalid/apply",
            content_hash=f"hash-{index}",
            location_kind=location_kind,
        )
        db.add(job)
        db.flush()
        db.add(Score(job_id=job.id, match_score=80 + (index % 10), verdict={"rationale": "ok"}))
        db.add(
            TailoringRun(
                job_id=job.id,
                output={},
                whitelist_passed=True,
                pdf_path=f"/tmp/job-{job.id}.pdf",
                gate_warnings=[{"rule": "r", "severity": "flag", "detail": "d", "evidence": "e"}],
            )
        )
        db.add(Application(job_id=job.id, status="queued"))
    db.flush()


def test_the_query_count_does_not_grow_with_the_number_of_cards(client, db, count_selects) -> None:
    seed(db, 3)
    count_selects["n"] = 0
    client.get("/api/v1/queue")
    for_three = count_selects["n"]

    seed(db, 6)
    count_selects["n"] = 0
    client.get("/api/v1/queue")
    for_nine = count_selects["n"]

    assert for_three == for_nine, (
        f"{for_three} queries for 3 cards but {for_nine} for 9 — still N+1"
    )


def test_the_card_still_carries_everything_it_did_before(client, db) -> None:
    """The join must not cost the fields the loop used to fetch."""
    seed(db, 1)

    card = client.get("/api/v1/queue").json()[0]

    assert card["match_score"] >= 80
    assert card["has_pdf"] is True
    assert card["warning_count"] == 1


def test_the_response_is_still_a_bare_list(client, db) -> None:
    """The dashboard maps over the body directly. It stays an array."""
    seed(db, 2)

    assert isinstance(client.get("/api/v1/queue").json(), list)


def test_limit_and_offset_walk_the_queue(client, db) -> None:
    seed(db, 5)

    first = client.get("/api/v1/queue?limit=2").json()
    second = client.get("/api/v1/queue?limit=2&offset=2").json()

    assert len(first) == 2
    assert len(second) == 2
    assert {c["application_id"] for c in first}.isdisjoint({c["application_id"] for c in second})


def test_the_total_is_reported_so_nothing_looks_lost(client, db) -> None:
    seed(db, 5)

    response = client.get("/api/v1/queue?limit=2")

    assert response.headers["x-total-count"] == "5"


def test_the_limit_is_capped(client, db) -> None:
    """An unbounded page is how this degrades silently as the queue grows."""
    assert client.get("/api/v1/queue?limit=100000").status_code == 422


def test_a_negative_offset_is_rejected(client, db) -> None:
    assert client.get("/api/v1/queue?offset=-1").status_code == 422


def test_ordering_still_puts_india_first(client, db) -> None:
    seed(db, 2, location_kind="remote")
    seed(db, 1, location_kind="india")

    cards = client.get("/api/v1/queue?location=india,remote").json()

    assert cards[0]["location_kind"] == "india"
