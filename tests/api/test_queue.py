"""The API is enforcement layer one: a failed gate can be seen, never approved."""

import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, get_db
from jobpilot_shared.db.models import Application, Company, Job, Profile, Score, TailoringRun, User
from jobpilot_worker.fixtures import SAMPLE_FACTS


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db):
    """One passing card and one that failed the whitelist gate."""
    user = User(email="owner@localhost")
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, canonical_facts=SAMPLE_FACTS.model_dump(mode="json")))

    company = Company(name="Acme Corp", normalized_name="acme", discovered_via="seed")
    db.add(company)
    db.flush()

    made = {}
    for key, passed in (("good", True), ("bad", False)):
        job = Job(
            company_id=company.id,
            source="greenhouse",
            external_id=f"gh-{key}",
            ats_job_id=f"{key}-1",
            title=f"{key.title()} Engineer",
            location="Remote",
            description="Python and PostgreSQL.",
            apply_url="https://example.invalid/apply",
            content_hash=key,
        )
        db.add(job)
        db.flush()

        db.add(
            Score(job_id=job.id, match_score=88, verdict={"rationale": "solid", "keyword_gaps": []})
        )
        run = TailoringRun(
            job_id=job.id,
            output={
                "summary": "Backend engineer.",
                "tailored_bullets": [
                    {
                        "employment_index": 0,
                        "original": "Built REST endpoints for the billing service.",
                        "rewritten": "Built REST endpoints for billing, in Python.",
                        "skills_referenced": ["Python"],
                    }
                ],
                "skills_ordered_for_this_jd": ["Python"],
            },
            whitelist_passed=passed,
            gate_rejections=None
            if passed
            else [
                {
                    "rule": "unknown_skill",
                    "severity": "reject",
                    "detail": "Kubernetes is not canonical",
                    "evidence": "Kubernetes",
                }
            ],
            gate_warnings=[
                {
                    "rule": "unlisted_token",
                    "severity": "flag",
                    "detail": "REST is not declared",
                    "evidence": "REST",
                }
            ],
            attempt=1 if passed else 3,
        )
        db.add(run)
        db.flush()

        application = Application(
            job_id=job.id,
            tailoring_run_id=run.id,
            status="queued" if passed else "needs_human",
        )
        db.add(application)
        db.flush()
        made[key] = application.id
    return made


def test_queue_lists_both_cards(client, seeded) -> None:
    response = client.get("/api/queue")
    assert response.status_code == 200
    cards = response.json()
    assert len(cards) == 2
    assert {c["status"] for c in cards} == {"queued", "needs_human"}


def test_card_detail_exposes_the_diff(client, seeded) -> None:
    response = client.get(f"/api/queue/{seeded['good']}")
    assert response.status_code == 200
    body = response.json()
    assert body["whitelist_passed"] is True
    assert len(body["diffs"]) == 1
    diff = body["diffs"][0]
    assert diff["original"].startswith("Built REST endpoints for the billing")
    assert diff["rewritten"].endswith("in Python.")
    assert diff["changed"] is True
    assert diff["company"] == "Acme Corp"


def test_failed_card_is_visible_with_its_rejections(client, seeded) -> None:
    """The human must be able to see *why* it failed."""
    body = client.get(f"/api/queue/{seeded['bad']}").json()
    assert body["whitelist_passed"] is False
    assert body["rejections"][0]["rule"] == "unknown_skill"
    assert body["attempts"] == 3


def test_warnings_are_surfaced_for_review(client, seeded) -> None:
    body = client.get(f"/api/queue/{seeded['good']}").json()
    assert body["warnings"][0]["rule"] == "unlisted_token"


def test_passing_card_can_be_approved(client, seeded) -> None:
    response = client.post(f"/api/queue/{seeded['good']}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_failed_card_cannot_be_approved(client, seeded) -> None:
    """Layer one. The client cannot talk its way past the gate."""
    response = client.post(f"/api/queue/{seeded['bad']}/approve")
    assert response.status_code == 409
    assert "whitelist gate" in response.json()["detail"]


def test_failed_card_cannot_be_marked_applied(client, seeded) -> None:
    assert client.post(f"/api/queue/{seeded['bad']}/applied").status_code == 409


def test_failed_card_can_be_rejected(client, seeded) -> None:
    """Rejecting is always allowed — it is how the human clears the queue."""
    response = client.post(f"/api/queue/{seeded['bad']}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_pdf_is_not_served_for_a_failed_gate(client, seeded) -> None:
    assert client.get(f"/api/queue/{seeded['bad']}/pdf").status_code in (404, 409)


def test_unknown_application_is_404(client, seeded) -> None:
    assert client.get("/api/queue/999999").status_code == 404
    assert client.post("/api/queue/999999/approve").status_code == 404
