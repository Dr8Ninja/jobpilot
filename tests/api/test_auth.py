"""Token auth, and the user scoping that makes `users`/`profiles` load-bearing.

Auth is off by default. This is a tool that has always run on localhost, and
turning a lock on by surprise would lock the owner out of their own queue. What
matters is that it *can* be turned on before the port is reachable from anywhere
else — today anyone who can reach it can approve applications and download the
user's resume.
"""

import json

import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, create_app, get_db
from jobpilot_shared.db.models import Application, Company, Job, Profile, User
from jobpilot_shared.settings import get_settings
from jobpilot_worker.fixtures import SAMPLE_FACTS


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def secured(db, monkeypatch):
    monkeypatch.setenv("JOBPILOT_AUTH_ENABLED", "true")
    monkeypatch.setenv("JOBPILOT_API_TOKEN", "s3cret-token")
    get_settings(refresh=True)
    secured_app = create_app()
    secured_app.dependency_overrides[get_db] = lambda: db
    return TestClient(secured_app)


def test_auth_is_off_by_default_so_nothing_breaks_today(client) -> None:
    assert client.get("/api/queue").status_code == 200


def test_a_secured_api_refuses_an_anonymous_request(secured) -> None:
    response = secured.get("/api/v1/queue")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_secured_api_refuses_the_wrong_token(secured) -> None:
    response = secured.get("/api/v1/queue", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


def test_a_secured_api_accepts_the_configured_token(secured) -> None:
    response = secured.get("/api/v1/queue", headers={"Authorization": "Bearer s3cret-token"})

    assert response.status_code == 200


def test_the_legacy_paths_are_secured_too(secured) -> None:
    """A compatibility shim that skips the lock is not a lock."""
    assert secured.get("/api/queue").status_code == 401


def test_health_stays_open_so_a_probe_can_reach_it(secured) -> None:
    assert secured.get("/api/v1/health").status_code == 200


def test_enabling_auth_without_a_token_refuses_to_start(monkeypatch) -> None:
    """Failing closed at boot beats serving an API whose lock has no key."""
    monkeypatch.setenv("JOBPILOT_AUTH_ENABLED", "true")
    monkeypatch.setenv("JOBPILOT_API_TOKEN", "")
    get_settings(refresh=True)

    with pytest.raises(RuntimeError, match="api_token"):
        create_app()


# ---------------------------------------------------------------------------
# user_id scoping
# ---------------------------------------------------------------------------


def make_application(db, *, user_id: int | None, external_id: str) -> Application:
    company = db.query(Company).filter_by(normalized_name="acme").one_or_none()
    if company is None:
        company = Company(name="Acme Corp", normalized_name="acme", discovered_via="seed")
        db.add(company)
        db.flush()
    job = Job(
        company_id=company.id,
        source="greenhouse",
        external_id=external_id,
        title="Backend Engineer",
        location="Remote",
        description="Python.",
        apply_url="https://example.invalid/apply",
        content_hash=external_id,
        location_kind="remote",
    )
    db.add(job)
    db.flush()
    row = Application(job_id=job.id, status="queued", user_id=user_id)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def owner(db) -> User:
    user = User(email="owner@localhost")
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, canonical_facts=json.loads(SAMPLE_FACTS.model_dump_json())))
    db.flush()
    return user


def test_the_queue_shows_the_owners_applications(client, db, owner) -> None:
    make_application(db, user_id=owner.id, external_id="mine")

    cards = client.get("/api/v1/queue").json()

    assert [c["title"] for c in cards] == ["Backend Engineer"]


def test_another_users_application_is_not_in_the_queue(client, db, owner) -> None:
    stranger = User(email="someone-else@example.invalid")
    db.add(stranger)
    db.flush()
    make_application(db, user_id=owner.id, external_id="mine")
    make_application(db, user_id=stranger.id, external_id="theirs")

    cards = client.get("/api/v1/queue").json()

    assert len(cards) == 1


def test_an_unowned_row_stays_visible(client, db, owner) -> None:
    """Rows predate the column. Nothing is ever deleted, and nothing silently
    disappears from the queue either."""
    make_application(db, user_id=None, external_id="legacy")

    assert len(client.get("/api/v1/queue").json()) == 1


def test_the_counts_are_scoped_the_same_way(client, db, owner) -> None:
    stranger = User(email="someone-else@example.invalid")
    db.add(stranger)
    db.flush()
    make_application(db, user_id=owner.id, external_id="mine")
    make_application(db, user_id=stranger.id, external_id="theirs")

    counts = {row["status"]: row["count"] for row in client.get("/api/v1/queue/counts").json()}

    assert counts["queued"] == 1


def test_another_users_card_cannot_be_opened(client, db, owner) -> None:
    stranger = User(email="someone-else@example.invalid")
    db.add(stranger)
    db.flush()
    theirs = make_application(db, user_id=stranger.id, external_id="theirs")

    assert client.get(f"/api/v1/queue/{theirs.id}").status_code == 404


def test_another_users_card_cannot_be_rejected(client, db, owner) -> None:
    stranger = User(email="someone-else@example.invalid")
    db.add(stranger)
    db.flush()
    theirs = make_application(db, user_id=stranger.id, external_id="theirs")

    assert client.post(f"/api/v1/queue/{theirs.id}/reject").status_code == 404


def test_the_profile_is_looked_up_by_user_not_by_luck(db, owner) -> None:
    """`select(Profile)` returned whichever row Postgres felt like. The facts a
    resume is checked against must belong to the person applying."""
    from jobpilot_worker.pipeline import load_facts

    stranger = User(email="someone-else@example.invalid")
    db.add(stranger)
    db.flush()
    wrong = json.loads(SAMPLE_FACTS.model_dump_json())
    wrong["identity"]["name"] = "Someone Else"
    db.add(Profile(user_id=stranger.id, canonical_facts=wrong))
    db.flush()

    assert load_facts(db, user_id=owner.id).identity.name == SAMPLE_FACTS.identity.name


def test_a_new_application_is_stamped_with_its_owner(db, owner) -> None:
    from jobpilot_shared.ownership import resolve_owner

    assert resolve_owner(db).id == owner.id
