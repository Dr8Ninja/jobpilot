"""A pipeline run is a durable record, not a log line.

The point of the table is that work moved off the request thread stays
observable: something has to hold the status while the worker is busy, and hold
the error afterwards if it failed. These tests pin the shape the API polls.
"""

import datetime as dt

import pytest
from jobpilot_shared.db.models import PipelineRun
from sqlalchemy.exc import IntegrityError


def test_a_new_run_starts_pending(db) -> None:
    run = PipelineRun(kind="pipeline")
    db.add(run)
    db.flush()
    db.refresh(run)

    assert run.status == "pending"
    assert run.started_at is None
    assert run.finished_at is None
    assert run.error is None


def test_status_is_constrained_by_the_database(db) -> None:
    db.add(PipelineRun(kind="pipeline", status="wishful"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_kind_is_constrained_by_the_database(db) -> None:
    db.add(PipelineRun(kind="mine-bitcoin"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_finished_run_carries_its_summary(db) -> None:
    """The summary is what the dashboard shows when polling stops."""
    now = dt.datetime.now(dt.UTC)
    run = PipelineRun(
        kind="pipeline",
        status="succeeded",
        started_at=now,
        finished_at=now,
        summary={"tailored_ok": 3, "text": "boards=94 jobs+12"},
    )
    db.add(run)
    db.flush()
    db.refresh(run)

    assert run.summary["tailored_ok"] == 3


def test_a_failed_run_keeps_the_error_rather_than_vanishing(db) -> None:
    run = PipelineRun(kind="tailor", status="failed", error="provider timed out")
    db.add(run)
    db.flush()
    db.refresh(run)

    assert run.status == "failed"
    assert run.error == "provider timed out"
