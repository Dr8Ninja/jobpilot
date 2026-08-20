"""Celery app, tasks, and the nightly beat schedule.

Deliberately thin. All the work — and all the bookkeeping — lives in `runs.py`,
which knows nothing about Celery, so the runner stays testable without a broker
and the CLI can call straight into it for inline runs.

Task names are pinned strings rather than derived from the module path. A name
is a wire format: rename one and whatever is already queued under the old name
is stranded, which shows up as "the nightly run quietly stopped happening".
"""

import logging

from celery import Celery
from celery.schedules import crontab
from jobpilot_shared.settings import get_settings

log = logging.getLogger(__name__)

EXECUTE_RUN_TASK = "jobpilot.execute_run"
NIGHTLY_TASK = "jobpilot.nightly_pipeline"


def build_celery_app() -> Celery:
    """Construct the app from current settings.

    A function rather than import-time construction so tests can build one
    against a different Redis database, and so the schedule reflects the
    environment the worker actually starts in.
    """
    settings = get_settings()
    celery_app = Celery("jobpilot", broker=settings.broker_url(), backend=settings.result_backend())
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone=settings.celery_timezone,
        enable_utc=True,
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=False,
        # One long tailoring is 3 attempts x 180s. Prefetching a second one
        # behind it just makes the queue lie about when work will start.
        worker_prefetch_multiplier=1,
        task_acks_late=True,
    )

    @celery_app.task(name=EXECUTE_RUN_TASK)
    def execute_run_task(run_id: int) -> str:
        from .runs import execute_run

        return execute_run(run_id)

    @celery_app.task(name=NIGHTLY_TASK)
    def nightly_pipeline_task() -> int:
        """Create tonight's run row, then execute it.

        Beat triggers this rather than `execute_run` directly, because beat has
        nothing to create a run row with — and a nightly pass with no row is a
        pass nobody can look at in the morning.
        """
        from jobpilot_shared.db.session import session_scope

        from .runs import create_run, execute_run

        with session_scope() as session:
            run = create_run(session, "pipeline", params={"trigger": "beat"})
            session.commit()
            run_id = run.id
        execute_run(run_id)
        return run_id

    if settings.nightly_run_enabled:
        celery_app.conf.beat_schedule = {
            "nightly-pipeline": {
                "task": NIGHTLY_TASK,
                "schedule": crontab(
                    hour=settings.nightly_run_hour, minute=settings.nightly_run_minute
                ),
            }
        }
    else:
        celery_app.conf.beat_schedule = {}

    return celery_app


#: The app `celery -A jobpilot_worker.celery_app worker` and `... beat` bind to.
app = build_celery_app()


def enqueue_run(run_id: int) -> None:
    """Hand a created run to the worker.

    Split out so the API depends on this one function rather than on Celery, and
    so the failure mode when Redis is down is one place to look.
    """
    app.send_task(EXECUTE_RUN_TASK, args=[run_id])
