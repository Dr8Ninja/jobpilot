import pytest
from jobpilot_shared.settings import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Settings reads a .env file by default; keep the repo's out of these tests."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "JOBPILOT_FIXTURE_MODE",
        "FIXTURE_MODE",
        "ANTHROPIC_API_KEY",
        "JOBPILOT_MATCH_SCORE_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults_match_the_phase_0_dials() -> None:
    s = Settings()
    assert s.match_score_threshold == 70
    assert s.max_tailored_per_day == 12
    assert s.embed_top_k == 40
    assert s.fixture_mode is False


def test_models_are_pinned_without_date_suffixes() -> None:
    s = Settings()
    assert s.tailoring_model == "claude-sonnet-5"
    assert s.embedding_model == "voyage-3"
    assert s.embedding_dimensions == 1024


def test_credentials_default_empty_so_fixture_mode_can_run(monkeypatch) -> None:
    s = Settings()
    assert s.anthropic_api_key == ""
    assert s.voyage_api_key == ""


@pytest.mark.parametrize("var", ["JOBPILOT_FIXTURE_MODE", "FIXTURE_MODE"])
def test_fixture_mode_accepts_prefixed_and_bare_env_var(monkeypatch, var: str) -> None:
    monkeypatch.setenv(var, "1")
    assert Settings().fixture_mode is True


def test_dials_are_overridable(monkeypatch) -> None:
    monkeypatch.setenv("JOBPILOT_MATCH_SCORE_THRESHOLD", "85")
    assert Settings().match_score_threshold == 85
