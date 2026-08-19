"""A truncated completion is a retry, not a mystery validation error.

Measured on `nvidia/nemotron-3-super-120b-a12b`: on roughly one scoring input in
five it stops emitting JSON mid-object and pads with whitespace until it hits the
token ceiling — 6,713 characters of padding in one capture, burning all 8,000
tokens and 58 seconds to return an unparseable body. It surfaced in the logs as
"returned a payload that failed ScoreVerdict validation", which pointed at the
schema rather than at the real cause.
"""

import types

import pytest
from jobpilot_shared.scoring_io import ScoreVerdict
from jobpilot_worker.clients.openai_compat import OpenAICompatLLMClient, TruncatedCompletion

VALID = (
    '{"must_have_coverage": ["Python: met"], "keyword_gaps": ["Kubernetes"],'
    ' "seniority_fit": "good", "rationale": "Solid match.", "fit_band": "strong",'
    ' "match_score": 78, "should_apply": true}'
)


def _client(monkeypatch, bodies, finishes):
    """A client whose transport replays the given bodies and finish reasons."""
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    from jobpilot_shared.settings import get_settings

    get_settings(refresh=True)
    client = OpenAICompatLLMClient(fallback_models=[], timeout=5)
    calls = {"n": 0}

    def fake_create(**kwargs):
        i = min(calls["n"], len(bodies) - 1)
        calls["n"] += 1
        message = types.SimpleNamespace(content=bodies[i])
        choice = types.SimpleNamespace(message=message, finish_reason=finishes[i])
        return types.SimpleNamespace(choices=[choice])

    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fake_create))
    )
    return client, calls


def test_trailing_whitespace_padding_does_not_fail_validation(monkeypatch) -> None:
    """The body is valid JSON; thousands of trailing newlines must not break it."""
    padded = VALID + "\n \n \n" * 500
    client, calls = _client(monkeypatch, [padded], ["stop"])
    verdict = client.parse(
        model="m", max_tokens=3000, system="s", prompt="p", output_format=ScoreVerdict
    )
    assert verdict.fit_band == "strong"
    assert calls["n"] == 1, "a padded but valid body must not cost a retry"


def test_a_truncated_completion_is_retried_on_the_same_model(monkeypatch) -> None:
    truncated = '{"must_have_coverage": [], "keyword_gaps": ["Kubernetes"'
    client, calls = _client(monkeypatch, [truncated, VALID], ["length", "stop"])
    verdict = client.parse(
        model="m", max_tokens=3000, system="s", prompt="p", output_format=ScoreVerdict
    )
    assert verdict.fit_band == "strong"
    assert calls["n"] == 2, "the truncated attempt must be retried, not abandoned"


def test_persistent_truncation_names_the_cause_in_the_error(monkeypatch) -> None:
    """The old message blamed the schema; the ceiling is the real cause."""
    truncated = '{"must_have_coverage": ['
    client, _ = _client(monkeypatch, [truncated], ["length"])
    with pytest.raises(Exception) as exc:
        client.parse(model="m", max_tokens=3000, system="s", prompt="p", output_format=ScoreVerdict)
    assert "token ceiling" in str(exc.value)


def test_truncated_completion_is_its_own_error_type() -> None:
    """So the retry policy can tell it apart from a timeout or a 404."""
    assert issubclass(TruncatedCompletion, RuntimeError)


def test_an_empty_body_still_fails_fast(monkeypatch) -> None:
    client, _ = _client(monkeypatch, ["   "], ["stop"])
    with pytest.raises(Exception) as exc:
        client.parse(model="m", max_tokens=3000, system="s", prompt="p", output_format=ScoreVerdict)
    assert "empty body" in str(exc.value)


def test_a_recovered_truncation_is_not_logged_as_a_warning(monkeypatch, caplog) -> None:
    """The user sees the console; a handled retry is not something to alarm them.

    A real failure is still loud — the aggregate error is raised and logged by
    the caller when every attempt is exhausted.
    """
    import logging

    truncated = '{"must_have_coverage": ['
    client, _ = _client(monkeypatch, [truncated, VALID], ["length", "stop"])
    with caplog.at_level(logging.WARNING, logger="jobpilot_worker.clients.openai_compat"):
        client.parse(model="m", max_tokens=3000, system="s", prompt="p", output_format=ScoreVerdict)
    assert not caplog.records, f"recovered truncation should not warn: {caplog.records}"
