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
from jobpilot_shared.db.models import Application, Company, Event, Job, Profile
from jobpilot_shared.settings import get_settings
from sqlalchemy import select
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
    embedded: int = 0
    scored: int = 0
    selected: int = 0
    not_selected: int = 0
    tailored_ok: int = 0
    tailored_needs_human: int = 0
    pdfs_rendered: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"boards={self.boards_pulled} (failed {self.board_failures}) "
            f"jobs+{self.jobs_inserted} deduped={self.jobs_deduped} "
            f"embedded={self.embedded} scored={self.scored} "
            f"selected={self.selected} shortlisted={self.not_selected} "
            f"tailored={self.tailored_ok} needs_human={self.tailored_needs_human} "
            f"pdfs={self.pdfs_rendered}"
        )


def load_facts(session: Session) -> CanonicalFacts:
    profile = session.scalar(select(Profile))
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
            outcome = ingest.ingest_ats_job(session, raw)
            report.jobs_inserted += outcome.inserted
            report.jobs_deduped += outcome.deduped

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
                outcome = ingest.ingest_remote_listing(session, board, listing)
                report.jobs_inserted += outcome.inserted

    has_aggregator = settings.fixture_mode or (settings.adzuna_app_id and settings.adzuna_app_key)
    if has_aggregator:
        queries = (
            ("software engineer",)
            if settings.fixture_mode
            else (
                "software engineer",
                "backend engineer",
                "python developer",
            )
        )
        for query in queries:
            found = discover_aggregator.search(query, where="India", fetch_fn=fetch_fn)
            if not found.ok:
                report.notes.append(f"aggregator '{query}': {found.error}")
                continue
            for listing in found.listings:
                resolved = resolve.resolve_listing(listing, fetch_fn=fetch_fn)
                outcome = ingest.ingest_resolved_listing(session, resolved)
                report.jobs_inserted += outcome.inserted
                report.jobs_deduped += outcome.deduped
                report.notes.extend(outcome.notes)
    else:
        report.notes.append("aggregator skipped: ADZUNA credentials not configured")
    session.flush()


def run_pipeline(
    session: Session,
    *,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    storage_dir: pathlib.Path | None = None,
) -> PipelineReport:
    llm = llm or get_llm_client()
    embedder = embedder or get_embedding_client()
    storage = storage_dir or STORAGE_DIR
    report = PipelineReport()

    facts = load_facts(session)

    run_discovery(session, report)

    report.embedded = embed.embed_pending_jobs(session, embedder)

    already_scored = {
        job_id
        for (job_id,) in session.execute(
            select(Application.job_id).where(Application.job_id.is_not(None))
        )
    }
    candidates = embed.prefilter(session, facts, embedder, exclude_job_ids=already_scored)

    scores = score.score_candidates(session, facts, candidates, llm)
    report.scored = len(scores)

    selected = score.select_for_tailoring(scores)
    report.selected = len(selected)

    # Everything else that was scored still gets a row. Dropping it on the floor
    # would hide real matches that merely fell below the cut — the user reviews
    # these in their own tab and can promote any of them.
    selected_ids = {row.job_id for row in selected}
    for row in scores:
        if row.job_id in selected_ids:
            continue
        session.add(Application(job_id=row.job_id, status="not_selected"))
        report.not_selected += 1
    session.flush()

    for row in selected:
        job = session.get(Job, row.job_id)
        if job is None:
            continue
        gaps = (row.verdict or {}).get("keyword_gaps", [])
        attempt = tailor.tailor_job(facts, job, gaps, llm)
        run = tailor.persist_tailoring(session, job, attempt)

        if not attempt.passed:
            report.tailored_needs_human += 1
            session.add(Application(job_id=job.id, tailoring_run_id=run.id, status="needs_human"))
            session.flush()
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

        session.add(Application(job_id=job.id, tailoring_run_id=run.id, status="queued"))
        session.flush()

    session.add(Event(type="pipeline.completed", payload={"summary": report.summary()}))
    session.flush()
    log.info("Pipeline complete: %s", report.summary())
    return report
