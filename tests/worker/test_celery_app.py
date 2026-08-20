"""Celery wiring, and the nightly beat schedule that closes Phase 0.

Task *names* are pinned deliberately. A name is a wire format: renaming one
strands whatever is already sitting in the queue under the old name, and the
failure looks like "the nightly run silently stopped happening".
"""

import pytest
from jobpilot_shared.db.models import PipelineRun
from jobpilot_shared.settings import get_settings


@pytest.fixture
def celery_app(monkeypatch):
    monkeypatch.setenv("JOBPILOT_REDIS_URL", "redis://localhost:6379/9")
    get_settings(refresh=True)
    from jobpilot_worker.celery_app import build_celery_app

    return build_celery_app()


def test_the_broker_and_backend_come_from_settings(celery_app) -> None:
    assert celery_app.conf.broker_url == "redis://localhost:6379/9"
    assert celery_app.conf.result_backend == "redis://localhost:6379/9"


def test_both_tasks_are_registered_under_stable_names(celery_app) -> None:
    assert "jobpilot.execute_run" in celery_app.tasks
    assert "jobpilot.nightly_pipeline" in celery_app.tasks


def test_the_nightly_schedule_uses_the_configured_hour(monkeypatch) -> None:
    monkeypatch.setenv("JOBPILOT_NIGHTLY_RUN_HOUR", "3")
    monkeypatch.setenv("JOBPILOT_NIGHTLY_RUN_MINUTE", "45")
    get_settings(refresh=True)
    from jobpilot_worker.celery_app import build_celery_app

    entry = build_celery_app().conf.beat_schedule["nightly-pipeline"]

    assert entry["task"] == "jobpilot.nightly_pipeline"
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {45}


def test_the_nightly_schedule_can_be_turned_off(monkeypatch) -> None:
    """Volume is a bounded dial. Nothing runs on its own unless it is asked to."""
    monkeypatch.setenv("JOBPILOT_NIGHTLY_RUN_ENABLED", "false")
    get_settings(refresh=True)
    from jobpilot_worker.celery_app import build_celery_app

    schedule = build_celery_app().conf.beat_schedule

    assert schedule == {}


def test_the_task_runs_the_work_and_lands_the_result(celery_app, global_session, monkeypatch):
    """End to end through Celery's eager mode: no broker, real work, real database.

    Fixture mode gives discovery recorded payloads to ingest, so this exercises
    the whole path — task → execute_run → stage → committed rows — without a
    credential or a network call.
    """
    monkeypatch.setenv("JOBPILOT_FIXTURE_MODE", "true")
    get_settings(refresh=True)
    celery_app.conf.task_always_eager = True
    from jobpilot_worker.runs import create_run

    session = global_session()
    try:
        run = create_run(session, "discovery")
        session.commit()
        run_id = run.id
    finally:
        session.close()

    celery_app.tasks["jobpilot.execute_run"].apply(args=[run_id])

    session = global_session()
    try:
        stored = session.get(PipelineRun, run_id)
        assert stored.status == "succeeded", stored.error
        assert stored.summary["jobs_inserted"] > 0
        assert stored.finished_at is not None
    finally:
        session.close()
