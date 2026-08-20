"""The composite run: discovery → dedupe → embed → score → tailor → render.

Every stage below is individually invocable; this module is the "one command"
composition of them. Phase 0 closes by adding a Celery beat schedule that calls
`run_pipeline` — the nightly shape, without a 24-hour feedback loop while the
tailoring prompt is still being tuned.
"""

import logging
import pathlib
from dataclasses import dataclass, field

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Application, Company, Event, Job, Profile, Score
from jobpilot_shared.ownership import owner_id
from jobpilot_shared.settings import get_settings
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .clients.embeddings import EmbeddingClient, get_embedding_client
from .clients.llm import LLMClient, get_llm_client
from .stages import (
    discover_aggregator,
    discover_ats,
    discover_remote,
    embed,
    ingest,
    resolve,
    score,
    tailor,
)
from .stages.render import RenderFailed, render_pdf

log = logging.getLogger(__name__)

STORAGE_DIR = pathlib.Path("storage/resumes")


@dataclass
class PipelineReport:
    boards_pulled: int = 0
    board_failures: int = 0
    jobs_inserted: int = 0
    jobs_deduped: int = 0
    jobs_failed: int = 0
    embedded: int = 0
    scored: int = 0
    selected: int = 0
    not_selected: int = 0
    tailored_ok: int = 0
    tailored_needs_human: int = 0
    tailoring_failed: int = 0
    pdfs_rendered: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"boards={self.boards_pulled} (failed {self.board_failures}) "
            f"jobs+{self.jobs_inserted} deduped={self.jobs_deduped} "
            f"unstorable={self.jobs_failed} "
            f"embedded={self.embedded} scored={self.scored} "
            f"selected={self.selected} shortlisted={self.not_selected} "
            f"tailored={self.tailored_ok} needs_human={self.tailored_needs_human} "
            f"tailor_failed={self.tailoring_failed} "
            f"pdfs={self.pdfs_rendered}"
        )

    def as_dict(self) -> dict:
        """JSON for `pipeline_runs.summary`.

        `text` is the same one-liner the CLI prints, so a run reads identically
        whether you saw it in a terminal or are reading it back from the API.
        """
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__}
        payload["text"] = self.summary()
        return payload


def load_facts(session: Session, user_id: int | None = None) -> CanonicalFacts:
    """The immutable facts every tailored resume is checked against.

    Scoped to a user. This used to be `select(Profile)` — whichever row came
    back first — which is harmless with one profile and quietly wrong with two:
    the whitelist a resume is validated against has to belong to the person
    whose resume it is.
    """
    if user_id is None:
        user_id = owner_id(session)
    profile = session.get(Profile, user_id) if user_id is not None else None
    if profile is None:
        raise RuntimeError(
            "No confirmed canonical_facts. Run `jobpilot ingest-resume <pdf>`, edit "
            "profile/canonical_facts.json, then `jobpilot confirm-facts`."
        )
    return CanonicalFacts.model_validate(profile.canonical_facts)


def run_discovery(session: Session, report: PipelineReport) -> None:
    """Greenhouse boards first, then the aggregator — so dedupe has something to hit."""
    settings = get_settings()
    if settings.fixture_mode:
        from .fixtures import fixture_fetch

        fetch_fn = fixture_fetch
    else:
        from .clients.http import fetch as fetch_fn

    boards = [
        (c.ats_provider, c.board_token, c.name)
        for c in session.scalars(
            select(Company).where(
                Company.board_token.is_not(None), Company.ats_provider.is_not(None)
            )
        )
    ]
    for result in discover_ats.discover_boards(boards, fetch_fn=fetch_fn):
        report.boards_pulled += 1
        if not result.ok:
            report.board_failures += 1
            report.notes.append(f"{result.provider}/{result.board_token}: {result.error}")
            session.add(
                Event(
                    type="discovery.board_failed",
                    payload={
                        "provider": result.provider,
                        "board_token": result.board_token,
                        "error": result.error,
                    },
                )
            )
            continue
        for raw in result.jobs:
            outcome = ingest.ingest_one(session, ingest.ingest_ats_job, raw)
            report.jobs_inserted += outcome.inserted
            report.jobs_deduped += outcome.deduped
            report.jobs_failed += outcome.failed
            report.notes.extend(outcome.notes)

    # Keyless remote/global boards. Widen coverage beyond India-centric sources
    # without another credential.
    if not settings.fixture_mode:
        for board in settings.remote_boards_list():
            found = discover_remote.discover_remote_board(board, fetch_fn=fetch_fn)
            if not found.ok:
                report.notes.append(f"remote board {board}: {found.error}")
                continue
            report.boards_pulled += 1
            for listing in found.listings:
                outcome = ingest.ingest_one(session, ingest.ingest_remote_listing, board, listing)
                report.jobs_inserted += outcome.inserted
                report.jobs_failed += outcome.failed
                report.notes.extend(outcome.notes)

    has_aggregator = settings.fixture_mode or (settings.adzuna_app_id and settings.adzuna_app_key)
    if has_aggregator:
        queries = ("software engineer",) if settings.fixture_mode else settings.aggregator_queries
        # Every query runs against India and again against remote-anywhere, which
        # is the only way Adzuna surfaces a role that is open from India but not
        # indexed under an Indian city.
        for query in queries:
            for where in ("India", "Remote"):
                found = discover_aggregator.search(query, where=where, fetch_fn=fetch_fn)
                if not found.ok:
                    report.notes.append(f"aggregator '{query}' ({where}): {found.error}")
                    continue
                for listing in found.listings:
                    resolved = resolve.resolve_listing(listing, fetch_fn=fetch_fn)
                    outcome = ingest.ingest_one(session, ingest.ingest_resolved_listing, resolved)
                    report.jobs_inserted += outcome.inserted
                    report.jobs_deduped += outcome.deduped
                    report.jobs_failed += outcome.failed
                    report.notes.extend(outcome.notes)
    else:
        report.notes.append("aggregator skipped: ADZUNA credentials not configured")
    session.flush()


def tailor_application(
    session: Session,
    application_id: int,
    *,
    llm: LLMClient | None = None,
    storage_dir: pathlib.Path | None = None,
) -> dict:
    """Tailor one shortlisted card on demand.

    The nightly run only tailors what clears the threshold and fits the daily
    cap. This is the manual override: something in the shortlist looked worth
    pursuing, so it gets a tailored resume and joins the review queue.

    It lives here rather than in the API because it is a composition of stages,
    and because both the request handler and the Celery task need it. Up to
    three attempts at 180s each is far longer than a browser will wait, which is
    exactly why the caller enqueues this instead of running it inline.
    """
    application = session.get(Application, application_id)
    if application is None:
        raise LookupError(f"No application with id {application_id}")

    facts = load_facts(session, application.user_id)
    job = session.get(Job, application.job_id)
    latest_score = session.scalar(
        select(Score).where(Score.job_id == job.id).order_by(desc(Score.scored_at)).limit(1)
    )
    gaps = ((latest_score.verdict if latest_score else {}) or {}).get("keyword_gaps", [])

    attempt = tailor.tailor_job(facts, job, gaps, llm or get_llm_client(purpose="tailoring"))
    run = tailor.persist_tailoring(session, job, attempt)
    application.tailoring_run_id = run.id

    if not attempt.passed:
        # Layer two of the gate. A run that failed the fact-check is never
        # rendered and never reaches the review queue.
        application.status = "needs_human"
        session.commit()
        return {
            "application_id": application.id,
            "status": application.status,
            "whitelist_passed": False,
            "pdf_rendered": False,
        }

    rendered = False
    try:
        path = render_pdf(
            facts,
            attempt.output,
            (storage_dir or STORAGE_DIR) / f"job-{job.id}.pdf",
            target_company=job.company.name if job.company else None,
        )
        run.pdf_path = str(path)
        rendered = True
    except RenderFailed as exc:
        log.warning("PDF render failed for job %s: %s", job.id, exc)
        session.add(Event(job_id=job.id, type="render.failed", payload={"error": str(exc)}))

    application.status = "queued"
    session.commit()
    return {
        "application_id": application.id,
        "status": application.status,
        "whitelist_passed": True,
        "pdf_rendered": rendered,
    }


def run_pipeline(
    session: Session,
    *,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    storage_dir: pathlib.Path | None = None,
) -> PipelineReport:
    # Tailoring gets its own client: the shared fallback models answer a tailoring
    # request with an empty bullet list, which costs an attempt and yields an
    # untailored resume. An injected client (tests, fixtures) is used for both.
    tailoring_llm = llm or get_llm_client(purpose="tailoring")
    llm = llm or get_llm_client()
    embedder = embedder or get_embedding_client()
    storage = storage_dir or STORAGE_DIR
    report = PipelineReport()

    user_id = owner_id(session)
    facts = load_facts(session, user_id)

    run_discovery(session, report)
    # Commit after each expensive stage. Without this, a provider timeout in a
    # later stage rolls the whole run back — a live run lost 2,150 freshly
    # discovered jobs and their embeddings to one 90s LLM timeout during
    # tailoring. Discovery and embedding are the slow parts; they must survive.
    session.commit()

    report.embedded = embed.embed_pending_jobs(session, embedder)
    session.commit()

    already_scored = {
        job_id
        for (job_id,) in session.execute(
            select(Application.job_id).where(Application.job_id.is_not(None))
        )
    }
    candidates = embed.prefilter(session, facts, embedder, exclude_job_ids=already_scored)

    scores = score.score_candidates(session, facts, candidates, llm)
    report.scored = len(scores)

    # Selection needs the job rows themselves: seniority and location are read
    # off the posting, not off the verdict.
    scored_jobs = {c.job.id: c.job for c in candidates}
    selected = score.select_for_tailoring(scores, scored_jobs)
    report.selected = len(selected)

    # Everything else that was scored still gets a row. Dropping it on the floor
    # would hide real matches that merely fell below the cut — the user reviews
    # these in their own tab and can promote any of them.
    selected_ids = {row.job_id for row in selected}
    for row in scores:
        if row.job_id in selected_ids:
            continue
        session.add(Application(job_id=row.job_id, status="not_selected", user_id=user_id))
        report.not_selected += 1
    session.commit()

    for row in selected:
        job = session.get(Job, row.job_id)
        if job is None:
            continue
        job_id = job.id  # captured: the rollback below expires the instance
        gaps = (row.verdict or {}).get("keyword_gaps", [])
        try:
            attempt = tailor.tailor_job(facts, job, gaps, tailoring_llm)
        except Exception as exc:
            # One unlucky job must not cost the run. The provider times out often
            # enough that this is the normal path, not an edge case — the job
            # stays visible and can be retried from the dashboard.
            log.warning("Tailoring failed for job %s: %s", job_id, exc)
            session.rollback()
            session.add(Event(job_id=job_id, type="tailor.failed", payload={"error": str(exc)}))
            session.add(Application(job_id=job_id, status="not_selected", user_id=user_id))
            report.tailoring_failed += 1
            session.commit()
            continue
        run = tailor.persist_tailoring(session, job, attempt)

        if not attempt.passed:
            report.tailored_needs_human += 1
            session.add(
                Application(
                    job_id=job.id,
                    tailoring_run_id=run.id,
                    status="needs_human",
                    user_id=user_id,
                )
            )
            session.commit()
            continue

        report.tailored_ok += 1
        assert attempt.output is not None
        try:
            path = render_pdf(
                facts,
                attempt.output,
                storage / f"job-{job.id}.pdf",
                target_company=job.company.name if job.company else None,
            )
            run.pdf_path = str(path)
            report.pdfs_rendered += 1
        except RenderFailed as exc:
            log.warning("PDF render failed for job %s: %s", job.id, exc)
            session.add(Event(job_id=job.id, type="render.failed", payload={"error": str(exc)}))

        session.add(
            Application(job_id=job.id, tailoring_run_id=run.id, status="queued", user_id=user_id)
        )
        session.commit()

    session.add(Event(type="pipeline.completed", payload={"summary": report.summary()}))
    session.commit()
    log.info("Pipeline complete: %s", report.summary())
    return report
