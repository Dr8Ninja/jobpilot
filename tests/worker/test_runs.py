"""Bookkeeping for background runs.

The work itself is already tested stage by stage. What is new here is the
promise that *however* a run ends, the row says so — because once the work is
off the request thread, the row is the only thing the caller can see.
"""

import pytest
from jobpilot_shared.db.models import Job, PipelineRun
from jobpilot_worker.runs import create_run, execute_run


def test_a_created_run_is_pending_and_remembers_its_params(db) -> None:
    run = create_run(db, "tailor", params={"application_id": 7})
    db.flush()

    assert run.status == "pending"
    assert run.params == {"application_id": 7}


def test_a_successful_run_records_its_summary_and_timestamps(db) -> None:
    run = create_run(db, "pipeline")
    db.commit()

    execute_run(run.id, session=db, handler=lambda session, row: {"tailored_ok": 2})

    stored = db.get(PipelineRun, run.id)
    db.refresh(stored)
    assert stored.status == "succeeded"
    assert stored.summary == {"tailored_ok": 2}
    assert stored.started_at is not None
    assert stored.finished_at is not None
    assert stored.error is None


def test_a_failing_run_is_recorded_rather_than_raised(db) -> None:
    """The caller is long gone. Blowing up loses the only record of what happened."""

    def explode(session, row):
        raise RuntimeError("provider timed out")

    run = create_run(db, "pipeline")
    db.commit()

    execute_run(run.id, session=db, handler=explode)

    stored = db.get(PipelineRun, run.id)
    db.refresh(stored)
    assert stored.status == "failed"
    assert "provider timed out" in stored.error
    assert stored.finished_at is not None


def test_a_poisoned_transaction_still_gets_its_failure_written(db) -> None:
    """Postgres aborts the whole transaction on a failed statement.

    Without a rollback before the bookkeeping write, the UPDATE that marks the
    run failed fails too — and the run sits at `running` for ever. This is the
    same lesson `ingest_one` already learned.
    """

    def poison(session, row):
        # A NOT NULL violation, flushed: the transaction is now aborted.
        session.add(Job(company_id=None, source="greenhouse", external_id="x"))
        session.flush()

    run = create_run(db, "discovery")
    db.commit()

    execute_run(run.id, session=db, handler=poison)

    stored = db.get(PipelineRun, run.id)
    db.refresh(stored)
    assert stored.status == "failed"
    assert stored.error


def test_an_unknown_run_id_is_reported_not_guessed(db) -> None:
    with pytest.raises(LookupError):
        execute_run(999_999, session=db, handler=lambda session, row: {})


def test_a_tailor_run_without_an_application_id_fails_with_a_clear_error(db) -> None:
    run = create_run(db, "tailor", params={})
    db.commit()

    execute_run(run.id, session=db)

    stored = db.get(PipelineRun, run.id)
    db.refresh(stored)
    assert stored.status == "failed"
    assert "application_id" in stored.error


def test_the_report_serialises_to_something_the_summary_column_can_hold(db) -> None:
    from jobpilot_worker.pipeline import PipelineReport

    report = PipelineReport()
    report.tailored_ok = 3
    report.notes.append("aggregator skipped: ADZUNA credentials not configured")

    payload = report.as_dict()

    assert payload["tailored_ok"] == 3
    assert payload["notes"] == ["aggregator skipped: ADZUNA credentials not configured"]
    # `text` is what the CLI already prints, kept so the dashboard and the
    # terminal describe a run the same way.
    assert "tailored=3" in payload["text"]
