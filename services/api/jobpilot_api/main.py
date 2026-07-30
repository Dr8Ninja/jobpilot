"""FastAPI app backing the review dashboard.

The approvability filter lives here: a card whose tailoring run did not pass the
whitelist gate can be *seen* (so the human can inspect what went wrong) but can
never be approved. That is enforcement layer one; `render.py` re-checking the
gate itself is layer two, and the two fail independently.
"""

import datetime as dt
import pathlib

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from jobpilot_shared.db.models import Application, Company, Event, Job, Score, TailoringRun
from jobpilot_shared.db.session import get_session_factory
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .schemas import (
    BulletDiff,
    GateNote,
    QueueCard,
    QueueDetail,
    SkillGapRow,
    StatusCount,
)

app = FastAPI(title="JobPilot", version="0.1.0")

# The dashboard is a local Next.js dev server; this is a single-user local tool.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _notes(raw: list | None) -> list[GateNote]:
    return [GateNote(**item) for item in (raw or [])]


def _latest_run(session: Session, job_id: int) -> TailoringRun | None:
    return session.scalar(
        select(TailoringRun)
        .where(TailoringRun.job_id == job_id)
        .order_by(desc(TailoringRun.created_at))
        .limit(1)
    )


def _latest_score(session: Session, job_id: int) -> Score | None:
    return session.scalar(
        select(Score).where(Score.job_id == job_id).order_by(desc(Score.scored_at)).limit(1)
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/queue/counts", response_model=list[StatusCount])
def queue_counts(location: str | None = None, db: Session = Depends(get_db)):
    """Per-status totals, so the dashboard can show tabs without fetching everything.

    Takes the same `location` filter as the queue itself — otherwise the tab
    badges count rows the tab would not actually show.
    """
    statement = (
        select(Application.status, func.count(Application.id))
        .join(Job, Job.id == Application.job_id)
        .group_by(Application.status)
    )
    if location:
        kinds = [k.strip() for k in location.split(",") if k.strip()]
        statement = statement.where(Job.location_kind.in_(kinds))
    rows = db.execute(statement).all()
    counts = [StatusCount(status=status, count=count) for status, count in rows]

    # One extra pseudo-status so the dashboard can badge the Overseas tab
    # without a second round trip.
    overseas = db.scalar(
        select(func.count(Application.id))
        .join(Job, Job.id == Application.job_id)
        .where(Job.location_kind == "overseas")
    )
    counts.append(StatusCount(status="overseas", count=overseas or 0))
    return counts


@app.get("/api/queue", response_model=list[QueueCard])
def list_queue(
    status: str | None = None,
    location: str | None = None,
    db: Session = Depends(get_db),
):
    """`location` accepts a comma-separated list of location kinds.

    The dashboard uses it two ways: the Overseas tab asks for `overseas` alone,
    and every other tab asks for `india,remote` so the roles the user actually
    wants are not diluted by ones they cannot take.
    """
    statement = (
        select(Application, Job, Company)
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        # India first within each page, then most recently queued.
        .order_by((Job.location_kind != "india"), desc(Application.created_at))
    )
    if status:
        statement = statement.where(Application.status == status)
    if location:
        kinds = [k.strip() for k in location.split(",") if k.strip()]
        statement = statement.where(Job.location_kind.in_(kinds))

    cards: list[QueueCard] = []
    for application, job, company in db.execute(statement).all():
        run = _latest_run(db, job.id)
        score = _latest_score(db, job.id)
        cards.append(
            QueueCard(
                application_id=application.id,
                job_id=job.id,
                company=company.name,
                title=job.title,
                location=job.location,
                salary=job.salary,
                match_score=score.match_score if score else None,
                status=application.status,
                source=job.source,
                description_quality=job.description_quality,
                apply_url=job.apply_url,
                location_kind=job.location_kind,
                has_pdf=bool(run and run.pdf_path),
                warning_count=len((run.gate_warnings or []) if run else []),
                posted_at=job.posted_at,
                created_at=application.created_at,
            )
        )
    return cards


@app.get("/api/skill-gaps", response_model=list[SkillGapRow])
def skill_gaps(min_jobs: int = 1, db: Session = Depends(get_db)):
    """What to learn next, and who is asking for it.

    Built from the `keyword_gaps` the scoring stage already records per job, so
    it costs nothing extra. A gap never changes the resume — the whitelist gate
    still rejects any skill the candidate has not got. This is a reading list.
    """
    from jobpilot_shared.canonical_facts import CanonicalFacts
    from jobpilot_shared.db.models import Profile
    from jobpilot_shared.skill_gaps import aggregate_gaps

    profile = db.scalar(select(Profile))
    known: tuple[str, ...] = ()
    if profile is not None:
        known = CanonicalFacts.model_validate(profile.canonical_facts).skills

    rows: list[tuple[str, str, str, int]] = []
    query = (
        select(Score.verdict, Company.name, Job.title, Job.id)
        .join(Job, Job.id == Score.job_id)
        .join(Company, Company.id == Job.company_id)
    )
    for verdict, company, title, job_id in db.execute(query).all():
        for gap in (verdict or {}).get("keyword_gaps", []):
            if isinstance(gap, str):
                rows.append((gap, company, title, job_id))

    return [
        SkillGapRow(
            skill=gap.skill,
            job_count=gap.job_count,
            companies=gap.companies,
            examples=gap.examples,
        )
        for gap in aggregate_gaps(rows, known_skills=known, min_jobs=min_jobs)
    ]


@app.get("/api/queue/{application_id}", response_model=QueueDetail)
def get_card(application_id: int, db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    job = db.get(Job, application.job_id)
    company = db.get(Company, job.company_id)
    run = _latest_run(db, job.id)
    score = _latest_score(db, job.id)

    output = (run.output if run else {}) or {}
    verdict = (score.verdict if score else {}) or {}

    diffs: list[BulletDiff] = []
    for bullet in output.get("tailored_bullets", []):
        index = bullet.get("employment_index", 0)
        diffs.append(
            BulletDiff(
                employment_index=index,
                company=_employer_name(db, job, index),
                original=bullet.get("original", ""),
                rewritten=bullet.get("rewritten", ""),
                skills_referenced=bullet.get("skills_referenced", []),
                changed=bullet.get("original") != bullet.get("rewritten"),
            )
        )

    return QueueDetail(
        application_id=application.id,
        job_id=job.id,
        company=company.name,
        title=job.title,
        location=job.location,
        salary=job.salary,
        status=application.status,
        source=job.source,
        description_quality=job.description_quality,
        apply_url=job.apply_url,
        location_kind=job.location_kind,
        description=job.description,
        match_score=score.match_score if score else None,
        rationale=verdict.get("rationale"),
        must_have_coverage=verdict.get("must_have_coverage", []),
        keyword_gaps=verdict.get("keyword_gaps", []),
        seniority_fit=verdict.get("seniority_fit"),
        summary=output.get("summary", ""),
        diffs=diffs,
        skills_ordered=output.get("skills_ordered_for_this_jd", []),
        whitelist_passed=bool(run and run.whitelist_passed),
        warnings=_notes(run.gate_warnings if run else None),
        rejections=_notes(run.gate_rejections if run else None),
        attempts=run.attempt if run else 0,
        has_pdf=bool(run and run.pdf_path),
        posted_at=job.posted_at,
    )


def _employer_name(db: Session, job: Job, index: int) -> str:
    from jobpilot_shared.db.models import Profile

    profile = db.scalar(select(Profile))
    employment = ((profile.canonical_facts or {}) if profile else {}).get("employment", [])
    if 0 <= index < len(employment):
        return employment[index].get("company", "")
    return ""


def _transition(db: Session, application_id: int, status: str, *, require_gate: bool) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    if require_gate:
        run = _latest_run(db, application.job_id)
        if run is None or not run.whitelist_passed:
            # Layer one. Nothing that failed the fact-check can be approved,
            # regardless of what the client sends.
            raise HTTPException(
                409,
                "This tailoring run did not pass the whitelist gate and cannot be "
                "approved. Inspect the rejections, then re-run tailoring.",
            )

    now = dt.datetime.now(dt.UTC)
    previous = application.status
    application.status = status
    if status == "approved":
        application.approved_at = now
    elif status == "rejected":
        application.rejected_at = now
    elif status == "applied":
        application.applied_at = now

    db.add(
        Event(
            application_id=application.id,
            job_id=application.job_id,
            type=f"application.{status}",
            payload={"at": now.isoformat(), "from": previous},
        )
    )
    db.flush()
    return {"application_id": application.id, "status": application.status}


@app.post("/api/queue/{application_id}/approve")
def approve(application_id: int, db: Session = Depends(get_db)):
    return _transition(db, application_id, "approved", require_gate=True)


@app.post("/api/queue/{application_id}/reject")
def reject(application_id: int, db: Session = Depends(get_db)):
    return _transition(db, application_id, "rejected", require_gate=False)


@app.post("/api/queue/{application_id}/applied")
def mark_applied(application_id: int, db: Session = Depends(get_db)):
    """Phase 0 apply is manual: the human applies, then records it here.

    These events are the substrate the response-rate baseline is computed from.
    """
    return _transition(db, application_id, "applied", require_gate=True)


@app.post("/api/queue/{application_id}/tailor")
def tailor_now(application_id: int, db: Session = Depends(get_db)):
    """Tailor a shortlisted job on demand.

    The nightly run only tailors what clears the threshold and fits the daily
    cap. This is the manual override: you saw something in the shortlist worth
    pursuing, so it gets a tailored resume and joins the review queue.
    """
    from jobpilot_shared.canonical_facts import CanonicalFacts
    from jobpilot_shared.db.models import Profile
    from jobpilot_worker.clients.llm import get_llm_client
    from jobpilot_worker.pipeline import STORAGE_DIR
    from jobpilot_worker.stages.render import RenderFailed, render_pdf
    from jobpilot_worker.stages.tailor import persist_tailoring, tailor_job

    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    profile = db.scalar(select(Profile))
    if profile is None:
        raise HTTPException(409, "No confirmed canonical_facts. Run `jobpilot confirm-facts`.")
    facts = CanonicalFacts.model_validate(profile.canonical_facts)

    job = db.get(Job, application.job_id)
    score = _latest_score(db, job.id)
    gaps = ((score.verdict if score else {}) or {}).get("keyword_gaps", [])

    attempt = tailor_job(facts, job, gaps, get_llm_client(purpose="tailoring"))
    run = persist_tailoring(db, job, attempt)
    application.tailoring_run_id = run.id

    if not attempt.passed:
        application.status = "needs_human"
        db.flush()
        return {"application_id": application.id, "status": application.status}

    try:
        path = render_pdf(
            facts,
            attempt.output,
            STORAGE_DIR / f"job-{job.id}.pdf",
            target_company=job.company.name if job.company else None,
        )
        run.pdf_path = str(path)
    except RenderFailed as exc:
        db.add(Event(job_id=job.id, type="render.failed", payload={"error": str(exc)}))

    application.status = "queued"
    db.flush()
    return {"application_id": application.id, "status": application.status}


@app.post("/api/queue/{application_id}/restore")
def restore(application_id: int, db: Session = Depends(get_db)):
    """Move a rejected or needs-human card back into the queue.

    Nothing is ever deleted — rejecting is reversible, so a card dismissed in
    haste can always be brought back.
    """
    return _transition(db, application_id, "queued", require_gate=False)


@app.get("/api/queue/{application_id}/pdf")
def get_pdf(application_id: int, db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    run = _latest_run(db, application.job_id)
    if run is None or not run.pdf_path:
        raise HTTPException(404, "No PDF for this application")
    if not run.whitelist_passed:
        raise HTTPException(409, "Refusing to serve a PDF for output that failed the gate")

    path = pathlib.Path(run.pdf_path)
    if not path.exists():
        raise HTTPException(404, f"PDF missing on disk: {path}")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
