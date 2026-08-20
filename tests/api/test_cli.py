"""The CLI keeps working exactly as it did.

`run-pipeline` is how the pipeline has always been driven, and moving work to a
worker must not change that. Inline stays the default; `--enqueue` is opt-in.
"""

import json

import pytest
from jobpilot_api.cli import app as cli
from jobpilot_shared.db.models import PipelineRun, Profile, User
from jobpilot_worker.fixtures import SAMPLE_FACTS
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def fixture_env(monkeypatch, global_session):
    """Fixture mode plus a confirmed profile: a full run with no credentials."""
    monkeypatch.setenv("JOBPILOT_FIXTURE_MODE", "true")
    from jobpilot_shared.settings import get_settings

    get_settings(refresh=True)

    session = global_session()
    try:
        user = User(email="owner@localhost")
        session.add(user)
        session.flush()
        session.add(
            Profile(
                user_id=user.id,
                canonical_facts=json.loads(SAMPLE_FACTS.model_dump_json()),
            )
        )
        session.commit()
    finally:
        session.close()
    return global_session


def test_run_pipeline_still_runs_inline_and_prints_its_summary(fixture_env, tmp_path) -> None:
    result = runner.invoke(cli, ["run-pipeline", "--storage", str(tmp_path)])

    assert result.exit_code == 0, result.output
    # The same one-liner the command has always ended with.
    assert "boards=" in result.output
    assert "tailored=" in result.output


def test_an_inline_run_is_recorded_like_any_other(fixture_env, tmp_path) -> None:
    """A run started from a terminal should be as visible as one from the
    dashboard — otherwise the table only tells half the story."""
    runner.invoke(cli, ["run-pipeline", "--storage", str(tmp_path)])

    session = fixture_env()
    try:
        run = session.query(PipelineRun).one()
        assert run.kind == "pipeline"
        assert run.status == "succeeded"
        assert run.summary["text"].startswith("boards=")
    finally:
        session.close()


def test_enqueue_hands_the_run_over_without_doing_the_work(
    fixture_env, tmp_path, monkeypatch
) -> None:
    sent: list[int] = []
    monkeypatch.setattr("jobpilot_worker.celery_app.enqueue_run", sent.append)

    result = runner.invoke(cli, ["run-pipeline", "--enqueue", "--storage", str(tmp_path)])

    assert result.exit_code == 0, result.output
    session = fixture_env()
    try:
        run = session.query(PipelineRun).one()
        assert run.status == "pending"
        assert run.started_at is None
        assert sent == [run.id]
        assert str(run.id) in result.output
    finally:
        session.close()


def test_run_status_reads_a_run_back(fixture_env) -> None:
    session = fixture_env()
    try:
        run = PipelineRun(kind="pipeline", status="failed", error="provider timed out")
        session.add(run)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    result = runner.invoke(cli, ["run-status", str(run_id)])

    assert result.exit_code == 0, result.output
    assert "failed" in result.output
    assert "provider timed out" in result.output


def test_a_failed_inline_run_exits_non_zero(global_session, monkeypatch, tmp_path) -> None:
    """No profile means no canonical facts, which has always been a hard stop."""
    monkeypatch.setenv("JOBPILOT_FIXTURE_MODE", "true")
    from jobpilot_shared.settings import get_settings

    get_settings(refresh=True)

    result = runner.invoke(cli, ["run-pipeline", "--storage", str(tmp_path)])

    assert result.exit_code != 0
    assert "canonical_facts" in result.output
