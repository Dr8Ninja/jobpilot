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


def test_models_are_pinned_and_measured() -> None:
    """Defaults were chosen by benchmarking this account, not by reputation.

    GLM-5.2 and DeepSeek V4 Pro are listed by NVIDIA but never return a token on
    the free tier, so they must not be the defaults.

    Re-measured against the real schemas over two runs each: nemotron-3-super
    answered scoring 2/2 (8s, 22s) and tailoring 2/2 complete (79s, 43s), while
    the previous tailoring default `openai/gpt-oss-120b` failed half its runs at
    the 200s mark and `google/gemma-4-31b-it` failed all four.
    """
    s = Settings()
    assert s.llm_provider == "nvidia"
    assert s.tailoring_model == "nvidia/nemotron-3-super-120b-a12b"
    assert s.scoring_model == "nvidia/nemotron-3-super-120b-a12b"
    assert s.embedding_provider == "nvidia"
    assert s.embedding_model == "nvidia/nv-embedqa-e5-v5"
    assert s.embedding_dimensions == 1024


def test_fallback_chain_is_parsed() -> None:
    """A model that stops serving must not take the whole run down with it."""
    models = Settings().llm_fallback_models_list()
    assert len(models) >= 2
    assert all("/" in m for m in models)


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


def test_scoring_ceiling_is_sized_for_a_runaway_not_a_good_answer() -> None:
    """Measured completions: 848-2,297 tokens. The ceiling caps the waste.

    Nemotron pads with whitespace to whatever ceiling it is given, so 8,000
    bought nothing but a 58-second failure on the inputs that degenerate.
    """
    s = Settings()
    assert 2500 <= s.scoring_max_tokens <= 4000


def test_embedding_token_budget_stays_under_the_providers_hard_limit() -> None:
    """`nv-embedqa-e5-v5` rejects over 512 tokens and the estimate is a heuristic."""
    from jobpilot_worker.stages.embed import EMBEDDING_TOKEN_LIMIT

    assert Settings().embedding_token_budget < EMBEDDING_TOKEN_LIMIT
