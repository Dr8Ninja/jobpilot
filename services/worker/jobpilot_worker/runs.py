"""Background runs: create a row, do the work, record how it ended.

This module is the seam between *what* the work is (the stages, unchanged) and
*where* it happens. Once a run is enqueued, the caller is gone — a browser tab
closed, a terminal exited, beat's own trigger long since returned — so the
`pipeline_runs` row is the only surface anything can observe. Every exit path
therefore writes to it, including the ones that blow up.

Nothing here is Celery-aware. `tasks.py` is the thin Celery wrapper; the CLI
calls straight into `execute_run` for its inline mode. That keeps the whole
runner testable without a broker.
"""

import datetime as dt
import logging
import pathlib
from collections.abc import Callable

from jobpilot_shared.db.models import PipelineRun
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: A handler does the work and returns whatever belongs in `summary`.
Handler = Callable[[Session, PipelineRun], dict | None]


def create_run(session: Session, kind: str, params: dict | None = None) -> PipelineRun:
    """Record the intent to run. Commit before enqueueing, or the worker can
    win the race and look up a row that is not there yet."""
    run = PipelineRun(kind=kind, params=params or None)
    session.add(run)
    session.flush()
    return run


def _run_pipeline_handler(session: Session, run: PipelineRun) -> dict:
    from .pipeline import STORAGE_DIR, run_pipeline

    params = run.params or {}
    storage = pathlib.Path(params["storage_dir"]) if params.get("storage_dir") else STORAGE_DIR
    report = run_pipeline(session, storage_dir=storage)
    return report.as_dict()


def _run_discovery_handler(session: Session, run: PipelineRun) -> dict:
    from .pipeline import PipelineReport, run_discovery

    report = PipelineReport()
    run_discovery(session, report)
    session.commit()
    return report.as_dict()


def _run_tailor_handler(session: Session, run: PipelineRun) -> dict:
    from .pipeline import tailor_application

    application_id = (run.params or {}).get("application_id")
    if application_id is None:
        raise ValueError("A tailor run needs params.application_id")
    return tailor_application(session, int(application_id))


HANDLERS: dict[str, Handler] = {
    "pipeline": _run_pipeline_handler,
    "discovery": _run_discovery_handler,
    "tailor": _run_tailor_handler,
}


def execute_run(
    run_id: int,
    *,
    session: Session | None = None,
    handler: Handler | None = None,
) -> str:
    """Run `run_id` to completion and return its final status.

    Never raises for a failure *inside* the work: that failure is the run's
    result, and the row records it. A missing run id is a different thing — the
    caller asked about something that does not exist — and does raise.
    """
    if session is not None:
        return _execute(session, run_id, handler)

    from jobpilot_shared.db.session import get_session_factory

    owned = get_session_factory()()
    try:
        return _execute(owned, run_id, handler)
    finally:
        owned.close()


def _execute(session: Session, run_id: int, handler: Handler | None) -> str:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise LookupError(f"No pipeline_run with id {run_id}")

    run.status = "running"
    run.started_at = dt.datetime.now(dt.UTC)
    session.commit()

    # Captured now, used in the failure path. A failed flush expires the
    # instance, so reading `run.kind` after the fact re-queries — on a
    # transaction Postgres has already aborted, which turns the log line itself
    # into the thing that loses the record.
    kind = run.kind
    work = handler or HANDLERS[kind]

    try:
        summary = work(session, run)
    except Exception as exc:
        # Postgres aborts the entire transaction on a failed statement, so the
        # bookkeeping UPDATE below would fail too and leave the run stuck at
        # `running` for ever. Roll back first, then record — the same shape
        # `ingest_one` uses to keep one bad row from costing the whole stage.
        log.warning("Run %s (%s) failed: %s", run_id, kind, exc)
        session.rollback()
        run = session.get(PipelineRun, run_id)
        run.status = "failed"
        run.error = str(exc) or exc.__class__.__name__
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()
        return run.status

    run = session.get(PipelineRun, run_id)
    run.status = "succeeded"
    run.summary = summary or {}
    run.finished_at = dt.datetime.now(dt.UTC)
    session.commit()
    log.info("Run %s (%s) succeeded: %s", run_id, run.kind, run.summary)
    return run.status
