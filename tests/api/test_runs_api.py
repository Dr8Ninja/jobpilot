"""Triggering and polling background work.

The contract is narrow on purpose: POST creates a row and returns, GET reads it
back. Nothing waits. A tailoring is up to three attempts at 180 seconds, which
is far past the point where a browser has given up and the user is looking at a
spinner that will never resolve.
"""

import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, get_db, get_enqueue
from jobpilot_shared.db.models import Application, Company, Job, PipelineRun


@pytest.fixture
def enqueued() -> list[int]:
    return []


@pytest.fixture
def client(db, enqueued):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_enqueue] = lambda: enqueued.append
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def application(db) -> Application:
    company = Company(name="Acme Corp", normalized_name="acme", discovered_via="seed")
    db.add(company)
    db.flush()
    job = Job(
        company_id=company.id,
        source="greenhouse",
        external_id="gh-1",
        title="Backend Engineer",
        location="Remote",
        description="Python.",
        apply_url="https://example.invalid/apply",
        content_hash="abc",
    )
    db.add(job)
    db.flush()
    row = Application(job_id=job.id, status="not_selected")
    db.add(row)
    db.flush()
    return row


def test_triggering_a_run_returns_immediately_with_its_id(client, enqueued) -> None:
    response = client.post("/api/v1/runs", json={"kind": "pipeline"})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "pipeline"
    assert body["status"] == "pending"
    assert enqueued == [body["id"]]


def test_the_run_is_committed_before_it_is_enqueued(client, db, enqueued) -> None:
    """A worker can claim the job before the request returns. If the row is not
    committed yet, it looks up an id that does not exist."""
    response = client.post("/api/v1/runs", json={"kind": "discovery"})

    stored = db.get(PipelineRun, response.json()["id"])
    assert stored is not None
    assert enqueued == [stored.id]


def test_a_run_can_be_polled(client, db) -> None:
    run_id = client.post("/api/v1/runs", json={"kind": "pipeline"}).json()["id"]

    # What the worker would do, a moment later.
    stored = db.get(PipelineRun, run_id)
    stored.status = "succeeded"
    stored.summary = {"tailored_ok": 4, "text": "boards=94"}
    db.flush()

    body = client.get(f"/api/v1/runs/{run_id}").json()
    assert body["status"] == "succeeded"
    assert body["summary"]["tailored_ok"] == 4


def test_polling_an_unknown_run_is_a_404(client) -> None:
    assert client.get("/api/v1/runs/999999").status_code == 404


def test_an_unknown_kind_is_rejected(client) -> None:
    assert client.post("/api/v1/runs", json={"kind": "mine-bitcoin"}).status_code == 422


def test_a_tailor_run_needs_an_application(client) -> None:
    response = client.post("/api/v1/runs", json={"kind": "tailor"})

    assert response.status_code == 422
    assert "application_id" in response.json()["detail"]


def test_a_tailor_run_for_a_missing_application_is_a_404(client) -> None:
    response = client.post("/api/v1/runs", json={"kind": "tailor", "application_id": 999999})

    assert response.status_code == 404


def test_a_tailor_run_carries_the_application_id(client, application, enqueued) -> None:
    response = client.post(
        "/api/v1/runs", json={"kind": "tailor", "application_id": application.id}
    )

    assert response.status_code == 202
    assert response.json()["params"] == {"application_id": application.id}
    assert len(enqueued) == 1


def test_the_tailor_endpoint_enqueues_instead_of_blocking(client, application, enqueued) -> None:
    """Three attempts at 180s is not something a request handler should hold."""
    response = client.post(f"/api/queue/{application.id}/tailor")

    assert response.status_code == 202
    body = response.json()
    assert body["application_id"] == application.id
    assert body["run_id"]
    assert enqueued == [body["run_id"]]


def test_the_tailor_endpoint_still_404s_for_a_missing_card(client) -> None:
    assert client.post("/api/queue/999999/tailor").status_code == 404


def test_a_broker_that_is_down_says_so_rather_than_pretending(client, db) -> None:
    """Silently accepting work nobody will do is the worst available answer."""

    def refuse(run_id: int) -> None:
        raise OSError("Connection refused: redis://localhost:6379/0")

    app.dependency_overrides[get_enqueue] = lambda: refuse
    response = client.post("/api/v1/runs", json={"kind": "pipeline"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    # The row survives, so the run can be retried by hand rather than lost.
    assert (
        db.scalar(PipelineRun.__table__.select().where(PipelineRun.status == "pending")) is not None
    )
