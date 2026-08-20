"""The properties Phase A must not have broken.

Phase A changed session and commit semantics, rewrote the queue query, moved
tailoring onto a worker and stamped applications with an owner. Each of those is
a plausible way to break something the rest of the suite assumes rather than
checks. These are the checks.

Where an invariant is already covered elsewhere the test is not duplicated here;
what follows are the gaps.
"""

import pathlib

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, get_db
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Application, Company, Event, Job, Score, TailoringRun
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from jobpilot_worker.stages import score as score_stage
from jobpilot_worker.stages.render import render_pdf


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def a_job(db, *, external_id: str = "gh-1", location_kind: str = "remote") -> Job:
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
        description="Python and PostgreSQL.",
        apply_url="https://example.invalid/apply",
        content_hash=external_id,
        location_kind=location_kind,
    )
    db.add(job)
    db.flush()
    return job


# ---------------------------------------------------------------------------
# "A tailored resume renders on one page"
# ---------------------------------------------------------------------------


def test_the_tailored_resume_is_one_page(facts: CanonicalFacts, tmp_path) -> None:
    """One page is the format. A resume that spills onto a second is a different
    document from the one the user proof-read."""
    output = TailoringOutput(
        summary="Backend engineer with Python and PostgreSQL experience.",
        tailored_bullets=tuple(
            TailoredBullet(
                employment_index=index,
                original=bullet,
                rewritten=f"{bullet.rstrip('.')} using Python and PostgreSQL.",
                skills_referenced=("Python", "PostgreSQL"),
            )
            for index, role in enumerate(facts.employment)
            for bullet in role.bullets
        ),
        skills_ordered_for_this_jd=("Python", "PostgreSQL", "FastAPI"),
    )

    path = render_pdf(facts, output, tmp_path / "resume.pdf")

    with pdfplumber.open(path) as pdf:
        assert len(pdf.pages) == 1


# ---------------------------------------------------------------------------
# "Nothing is ever deleted"
# ---------------------------------------------------------------------------


def test_reject_and_restore_round_trip(client, db) -> None:
    job = a_job(db)
    application = Application(job_id=job.id, status="queued")
    db.add(application)
    db.flush()

    assert client.post(f"/api/v1/queue/{application.id}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/v1/queue/{application.id}/restore").json()["status"] == "queued"

    assert db.get(Application, application.id) is not None


def test_a_rejection_is_recorded_rather_than_erased(client, db) -> None:
    """The row keeps its rejected_at after being restored: what happened,
    happened, and the events table keeps both transitions."""
    job = a_job(db)
    application = Application(job_id=job.id, status="queued")
    db.add(application)
    db.flush()

    client.post(f"/api/v1/queue/{application.id}/reject")
    client.post(f"/api/v1/queue/{application.id}/restore")
    db.refresh(application)

    assert application.rejected_at is not None
    types = {e.type for e in db.query(Event).filter_by(application_id=application.id)}
    assert {"application.rejected", "application.queued"} <= types


#: Every tab the dashboard renders, from `apps/web/components/Tabs.tsx`.
DASHBOARD_TABS = ["queued", "approved", "applied", "not_selected", "needs_human", "rejected"]


@pytest.mark.parametrize("status", DASHBOARD_TABS)
def test_every_status_is_reachable_from_its_tab(client, db, status: str) -> None:
    job = a_job(db, external_id=f"gh-{status}")
    db.add(Application(job_id=job.id, status=status))
    db.flush()

    cards = client.get(f"/api/v1/queue?status={status}").json()

    assert [c["status"] for c in cards] == [status]


def test_the_all_tab_hides_nothing(client, db) -> None:
    for index, status in enumerate(DASHBOARD_TABS):
        job = a_job(db, external_id=f"gh-all-{index}")
        db.add(Application(job_id=job.id, status=status))
    db.flush()

    cards = client.get("/api/v1/queue").json()

    assert {c["status"] for c in cards} == set(DASHBOARD_TABS)


# ---------------------------------------------------------------------------
# "Location classification routes India and remote to the main tabs"
# ---------------------------------------------------------------------------


def test_overseas_roles_stay_out_of_the_main_queue_but_not_out_of_the_database(client, db) -> None:
    for kind in ("india", "remote", "overseas"):
        job = a_job(db, external_id=f"gh-{kind}", location_kind=kind)
        db.add(Application(job_id=job.id, status="queued"))
    db.flush()

    main = client.get("/api/v1/queue?location=india,remote").json()
    overseas = client.get("/api/v1/queue?location=overseas").json()

    assert {c["location_kind"] for c in main} == {"india", "remote"}
    assert {c["location_kind"] for c in overseas} == {"overseas"}


# ---------------------------------------------------------------------------
# "Per-row failures are absorbed, and stages commit as they finish"
# ---------------------------------------------------------------------------


class OneBadJob:
    """An LLM client that fails for exactly one job and answers for the rest."""

    def __init__(self, fail_on_title: str) -> None:
        self.fail_on_title = fail_on_title
        self.calls = 0

    def parse(self, *, model, max_tokens, system, prompt, output_format):
        self.calls += 1
        if self.fail_on_title in prompt:
            raise TimeoutError("provider timed out")
        return output_format.model_validate(
            {
                "fit_band": "strong",
                "must_have_coverage": [],
                "keyword_gaps": [],
                "seniority_fit": "good",
                "should_apply": True,
                "rationale": "Covers the stated requirements.",
                "match_score": 82,
            }
        )


class Candidate:
    def __init__(self, job: Job) -> None:
        self.job = job
        self.similarity = 0.9


def test_one_job_that_cannot_be_scored_does_not_cost_the_others(db, facts: CanonicalFacts) -> None:
    good = a_job(db, external_id="gh-good")
    bad = a_job(db, external_id="gh-bad")
    bad.title = "Poisoned Engineer"
    db.flush()

    scores = score_stage.score_candidates(
        db, facts, [Candidate(good), Candidate(bad)], OneBadJob("Poisoned Engineer")
    )

    assert [s.job_id for s in scores] == [good.id]
    assert db.query(Event).filter_by(type="score.failed", job_id=bad.id).count() == 1


def test_a_failure_after_a_commit_does_not_undo_the_committed_work(db) -> None:
    """Discovery and embedding are the slow stages, and a live run once lost
    2,150 freshly discovered jobs to one timeout during tailoring. Committing
    per stage is what stops that — and the rollback in the failure path must not
    reach back past the commit."""
    job = a_job(db, external_id="gh-committed")
    db.commit()

    try:
        db.add(Job(company_id=None, source="greenhouse", external_id="broken"))
        db.flush()
    except Exception:
        db.rollback()

    assert db.get(Job, job.id) is not None


def test_a_gate_failure_leaves_the_card_visible_and_unapprovable(client, db) -> None:
    """Both halves matter: `needs_human` is a tab, not a bin, and nothing that
    failed the fact-check can be approved from it."""
    job = a_job(db, external_id="gh-gate")
    run = TailoringRun(
        job_id=job.id,
        output={"summary": "", "tailored_bullets": [], "skills_ordered_for_this_jd": []},
        whitelist_passed=False,
        gate_rejections=[
            {
                "rule": "unknown_skill",
                "severity": "reject",
                "detail": "Kubernetes is not canonical",
                "evidence": "Kubernetes",
            }
        ],
    )
    db.add(run)
    db.flush()
    application = Application(job_id=job.id, tailoring_run_id=run.id, status="needs_human")
    db.add(application)
    db.add(Score(job_id=job.id, match_score=88, verdict={"rationale": "ok"}))
    db.flush()

    visible = client.get("/api/v1/queue?status=needs_human").json()
    assert [c["application_id"] for c in visible] == [application.id]

    assert client.post(f"/api/v1/queue/{application.id}/approve").status_code == 409
    assert client.post(f"/api/v1/queue/{application.id}/applied").status_code == 409
    assert client.get(f"/api/v1/queue/{application.id}/pdf").status_code in (404, 409)
    # Restoring it is still allowed: nothing is ever stuck.
    assert client.post(f"/api/v1/queue/{application.id}/restore").status_code == 200


def test_a_failed_gate_pdf_is_refused_even_when_the_file_exists(client, db, tmp_path) -> None:
    """The 404-when-missing path can hide the 409-when-forbidden path. This
    pins the refusal itself."""
    job = a_job(db, external_id="gh-gate-pdf")
    pdf = tmp_path / "job.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    run = TailoringRun(
        job_id=job.id,
        output={},
        whitelist_passed=False,
        pdf_path=str(pdf),
    )
    db.add(run)
    db.flush()
    application = Application(job_id=job.id, tailoring_run_id=run.id, status="needs_human")
    db.add(application)
    db.flush()

    response = client.get(f"/api/v1/queue/{application.id}/pdf")

    assert response.status_code == 409
    assert pathlib.Path(pdf).exists()
