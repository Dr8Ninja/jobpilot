"""FastAPI app backing the review dashboard.

The approvability filter lives here: a card whose tailoring run did not pass the
whitelist gate can be *seen* (so the human can inspect what went wrong) but can
never be approved. That is enforcement layer one; `render.py` re-checking the
gate itself is layer two, and the two fail independently.
"""

import datetime as dt
import logging
import pathlib
from collections.abc import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from jobpilot_shared.db.models import (
    Application,
    Company,
    Event,
    Job,
    PipelineRun,
    Score,
    TailoringRun,
    User,
)
from jobpilot_shared.settings import get_settings
from jobpilot_worker.runs import create_run
from sqlalchemy import desc, func, or_, select, true
from sqlalchemy.orm import Session

from .auth import current_user, require_token
from .deps import get_db
from .errors import install_error_handlers
from .middleware import RequestLogMiddleware, configure_logging
from .schemas import (
    BulletDiff,
    GateNote,
    QueueCard,
    QueueDetail,
    RunRequest,
    RunStatus,
    SkillGapRow,
    StatusCount,
    TailorAccepted,
)

log = logging.getLogger("jobpilot.api")

router = APIRouter()


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


def get_enqueue() -> Callable[[int], None]:
    """The seam between the API and the broker.

    A dependency rather than a direct import so a test can watch what would have
    been enqueued without standing up Redis, and so there is exactly one place
    to look when the queue is not moving.
    """
    from jobpilot_worker.celery_app import enqueue_run

    return enqueue_run


def _start_run(
    db: Session,
    enqueue: Callable[[int], None],
    kind: str,
    params: dict | None = None,
) -> PipelineRun:
    """Create the row, commit it, then hand it over — in that order.

    The worker can claim the task before this request has returned, so the row
    has to be durable first or the worker looks up an id that is not there yet.
    """
    run = create_run(db, kind, params)
    db.commit()
    try:
        enqueue(run.id)
    except Exception as exc:
        # The row stays `pending` and can be re-enqueued by hand. Reporting
        # success for work nobody will ever pick up is the worse failure.
        log.warning("Could not enqueue run %s: %s", run.id, exc)
        raise HTTPException(
            503,
            "Could not reach the task queue. Start Redis and the worker "
            "(`celery -A jobpilot_worker.celery_app worker`), then retry.",
        ) from exc
    return run


@router.post("/runs", response_model=RunStatus, status_code=202)
def start_run(
    request: RunRequest,
    db: Session = Depends(get_db),
    enqueue: Callable[[int], None] = Depends(get_enqueue),
    user: User | None = Depends(current_user),
):
    """Trigger background work. Returns at once; poll `GET /runs/{id}`."""
    params: dict | None = None
    if request.kind == "tailor":
        if request.application_id is None:
            raise HTTPException(422, "A tailor run needs an application_id")
        _owned_application(db, request.application_id, user)
        params = {"application_id": request.application_id}
    return _start_run(db, enqueue, request.kind, params)


@router.get("/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


def _visible_to(user: User | None):
    """The ownership predicate for every application query.

    A NULL `user_id` means the row predates the column. The owner still sees it:
    nothing in this system is ever deleted, and a card that vanished from the
    queue would be a deletion in all but name.
    """
    if user is None:
        return Application.user_id.is_(None)
    return or_(Application.user_id == user.id, Application.user_id.is_(None))


def _owned_application(db: Session, application_id: int, user: User | None) -> Application:
    """Fetch a card the caller is entitled to, or 404.

    Another user's card is reported as missing rather than as forbidden — a 403
    would confirm it exists.
    """
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    if user is not None and application.user_id not in (None, user.id):
        raise HTTPException(404, "Application not found")
    if user is None and application.user_id is not None:
        raise HTTPException(404, "Application not found")
    return application


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/queue/counts", response_model=list[StatusCount])
def queue_counts(
    location: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """Per-status totals, so the dashboard can show tabs without fetching everything.

    Takes the same `location` filter as the queue itself — otherwise the tab
    badges count rows the tab would not actually show.
    """
    statement = (
        select(Application.status, func.count(Application.id))
        .join(Job, Job.id == Application.job_id)
        .where(_visible_to(user))
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
        .where(Job.location_kind == "overseas", _visible_to(user))
    )
    counts.append(StatusCount(status="overseas", count=overseas or 0))
    return counts


#: One page of the queue. Generous enough that today's ~140 cards arrive whole,
#: bounded so this cannot quietly become "fetch everything" as the queue grows.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


@router.get("/queue", response_model=list[QueueCard])
def list_queue(
    response: Response,
    status: str | None = None,
    location: str | None = None,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """`location` accepts a comma-separated list of location kinds.

    The dashboard uses it two ways: the Overseas tab asks for `overseas` alone,
    and every other tab asks for `india,remote` so the roles the user actually
    wants are not diluted by ones they cannot take.

    The body is a bare array, exactly as it has always been; the unpaginated
    total travels in `X-Total-Count` so a truncated page is visible as
    truncation rather than as a queue that quietly got shorter.
    """
    # The latest run and the latest score used to be fetched per card, inside
    # the loop — two round trips per row. A correlated LATERAL gives Postgres the
    # same "newest first, take one" semantics as part of the single query.
    latest_run = (
        select(
            TailoringRun.pdf_path.label("pdf_path"),
            TailoringRun.gate_warnings.label("gate_warnings"),
        )
        .where(TailoringRun.job_id == Job.id)
        .order_by(desc(TailoringRun.created_at))
        .limit(1)
        .correlate(Job)
        .lateral("latest_run")
    )
    latest_score = (
        select(Score.match_score.label("match_score"))
        .where(Score.job_id == Job.id)
        .order_by(desc(Score.scored_at))
        .limit(1)
        .correlate(Job)
        .lateral("latest_score")
    )

    filters = [_visible_to(user)]
    if status:
        filters.append(Application.status == status)
    if location:
        kinds = [k.strip() for k in location.split(",") if k.strip()]
        filters.append(Job.location_kind.in_(kinds))

    total = db.scalar(
        select(func.count(Application.id)).join(Job, Job.id == Application.job_id).where(*filters)
    )
    response.headers["X-Total-Count"] = str(total or 0)

    statement = (
        select(
            Application,
            Job,
            Company,
            latest_run.c.pdf_path,
            latest_run.c.gate_warnings,
            latest_score.c.match_score,
        )
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        .outerjoin(latest_run, true())
        .outerjoin(latest_score, true())
        .where(*filters)
        # India first within each page, then most recently queued. `id` breaks
        # ties so paging is stable when several cards share a timestamp.
        .order_by(
            (Job.location_kind != "india"),
            desc(Application.created_at),
            desc(Application.id),
        )
        .limit(limit)
        .offset(offset)
    )

    return [
        QueueCard(
            application_id=application.id,
            job_id=job.id,
            company=company.name,
            title=job.title,
            location=job.location,
            salary=job.salary,
            match_score=match_score,
            status=application.status,
            source=job.source,
            description_quality=job.description_quality,
            apply_url=job.apply_url,
            location_kind=job.location_kind,
            has_pdf=bool(pdf_path),
            warning_count=len(gate_warnings or []),
            posted_at=job.posted_at,
            created_at=application.created_at,
        )
        for application, job, company, pdf_path, gate_warnings, match_score in db.execute(
            statement
        ).all()
    ]


@router.get("/skill-gaps", response_model=list[SkillGapRow])
def skill_gaps(
    min_jobs: int = 1,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """What to learn next, and who is asking for it.

    Built from the `keyword_gaps` the scoring stage already records per job, so
    it costs nothing extra. A gap never changes the resume — the whitelist gate
    still rejects any skill the candidate has not got. This is a reading list.
    """
    from jobpilot_shared.canonical_facts import CanonicalFacts
    from jobpilot_shared.db.models import Profile
    from jobpilot_shared.skill_gaps import aggregate_gaps

    profile = db.get(Profile, user.id) if user is not None else None
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


@router.get("/queue/{application_id}", response_model=QueueDetail)
def get_card(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    application = _owned_application(db, application_id, user)

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
                company=_employer_name(db, user, index),
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


def _employer_name(db: Session, user: User | None, index: int) -> str:
    from jobpilot_shared.db.models import Profile

    profile = db.get(Profile, user.id) if user is not None else None
    employment = ((profile.canonical_facts or {}) if profile else {}).get("employment", [])
    if 0 <= index < len(employment):
        return employment[index].get("company", "")
    return ""


def _transition(
    db: Session,
    application_id: int,
    status: str,
    *,
    require_gate: bool,
    user: User | None = None,
) -> dict:
    application = _owned_application(db, application_id, user)

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


@router.post("/queue/{application_id}/approve")
def approve(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    return _transition(db, application_id, "approved", require_gate=True, user=user)


@router.post("/queue/{application_id}/reject")
def reject(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    return _transition(db, application_id, "rejected", require_gate=False, user=user)


@router.post("/queue/{application_id}/applied")
def mark_applied(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """Phase 0 apply is manual: the human applies, then records it here.

    These events are the substrate the response-rate baseline is computed from.
    """
    return _transition(db, application_id, "applied", require_gate=True, user=user)


@router.post("/queue/{application_id}/tailor", response_model=TailorAccepted, status_code=202)
def tailor_now(
    application_id: int,
    db: Session = Depends(get_db),
    enqueue: Callable[[int], None] = Depends(get_enqueue),
    user: User | None = Depends(current_user),
):
    """Queue a tailoring for a shortlisted job.

    The nightly run only tailors what clears the threshold and fits the daily
    cap. This is the manual override: you saw something in the shortlist worth
    pursuing, so it gets a tailored resume and joins the review queue.

    This used to do the work inline. Tailoring is up to `max_tailoring_attempts`
    calls at `llm_timeout_seconds` each — nine minutes in the worst case — so
    the browser gave up long before the server did and the result became
    invisible. Now it returns a run to poll.
    """
    _owned_application(db, application_id, user)
    run = _start_run(db, enqueue, "tailor", {"application_id": application_id})
    return TailorAccepted(application_id=application_id, run_id=run.id, status=run.status)


@router.post("/queue/{application_id}/restore")
def restore(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """Move a rejected or needs-human card back into the queue.

    Nothing is ever deleted — rejecting is reversible, so a card dismissed in
    haste can always be brought back.
    """
    return _transition(db, application_id, "queued", require_gate=False, user=user)


@router.get("/queue/{application_id}/pdf")
def get_pdf(
    application_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    application = _owned_application(db, application_id, user)

    run = _latest_run(db, application.job_id)
    if run is None or not run.pdf_path:
        raise HTTPException(404, "No PDF for this application")
    if not run.whitelist_passed:
        raise HTTPException(409, "Refusing to serve a PDF for output that failed the gate")

    path = pathlib.Path(run.pdf_path)
    if not path.exists():
        raise HTTPException(404, f"PDF missing on disk: {path}")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


#: Everything is served twice. `/api/v1` is the real, documented surface; the
#: bare `/api` mount is a compatibility shim for the dashboard, which was
#: written against the unprefixed paths and must keep working unchanged. The
#: shim is hidden from the schema so each operation appears once.
API_PREFIX = "/api/v1"
LEGACY_PREFIX = "/api"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    if settings.auth_enabled and not settings.api_token:
        # Fail closed at boot. Serving an API whose lock has no key is worse
        # than not starting, because it looks secured.
        raise RuntimeError(
            "auth_enabled is on but api_token is empty. Set JOBPILOT_API_TOKEN, "
            "or turn JOBPILOT_AUTH_ENABLED off."
        )

    application = FastAPI(
        title="JobPilot",
        version="0.1.0",
        dependencies=[Depends(require_token)],
    )

    application.add_middleware(RequestLogMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    install_error_handlers(application)

    application.include_router(router, prefix=API_PREFIX)
    application.include_router(router, prefix=LEGACY_PREFIX, include_in_schema=False)
    return application


app = create_app()
