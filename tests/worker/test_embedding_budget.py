"""Embedding inputs must fit the provider's token cap in any script.

A character budget alone was wrong on real data: English runs ~4 characters per
token, Japanese and Korean closer to one token per character. A 1600-character
budget is ~400 tokens of English and ~1600 of Japanese, so every CJK posting was
rejected — 62 in one run, whole boards like Datadog Tokyo lost.
"""

import pytest
from jobpilot_shared.settings import get_settings
from jobpilot_worker.stages.embed import (
    EMBEDDING_TOKEN_LIMIT,
    estimated_tokens,
    fit_to_token_budget,
    job_text,
)


class FakeJob:
    def __init__(self, title: str, description: str, location: str = "Tokyo, Japan") -> None:
        self.id = 1
        self.title = title
        self.description = description
        self.location = location


JAPANESE = "データとAIの企業であり、全世界で組織がデータを活用しています。" * 60
KOREAN = "우리는 비즈니스를 성장시키고 관리하는 데 열정적인 분을 찾고 있습니다." * 60
ENGLISH = "We are looking for a backend engineer with strong Python skills. " * 60


def test_english_is_estimated_at_roughly_four_characters_per_token() -> None:
    assert 0.2 * len(ENGLISH) <= estimated_tokens(ENGLISH) <= 0.3 * len(ENGLISH)


def test_cjk_is_estimated_at_roughly_one_token_per_character() -> None:
    """The whole bug: this was previously assumed to be 4 characters per token."""
    assert estimated_tokens(JAPANESE) >= 0.8 * len(JAPANESE)


@pytest.mark.parametrize("text", [JAPANESE, KOREAN, ENGLISH])
def test_trimming_brings_any_script_under_the_budget(text: str) -> None:
    trimmed = fit_to_token_budget(text, 440)
    assert estimated_tokens(trimmed) <= 440
    assert trimmed, "trimming must not empty the text"


def test_trimming_leaves_short_text_untouched() -> None:
    assert fit_to_token_budget("Backend engineer", 440) == "Backend engineer"


@pytest.mark.parametrize(
    "title,description",
    [
        ("ソリューションアーキテクト (プリセールス)", JAPANESE),
        ("Enterprise Account Executive, Growth", KOREAN),
        ("Backend Engineer", ENGLISH),
    ],
)
def test_job_text_always_fits_the_providers_hard_limit(title: str, description: str) -> None:
    """The regression: these exact shapes were rejected with a 400 and dropped."""
    text = job_text(FakeJob(title, description))
    assert estimated_tokens(text) <= EMBEDDING_TOKEN_LIMIT
    assert text.startswith(title), "the title carries the signal and must survive"


def test_the_default_budget_leaves_headroom_under_the_hard_limit() -> None:
    """The estimate is a heuristic; a rejection costs the whole row."""
    assert get_settings().embedding_token_budget < EMBEDDING_TOKEN_LIMIT


def test_an_explicit_token_budget_overrides_the_default() -> None:
    """The retry path shrinks this until the provider accepts."""
    text = job_text(FakeJob("Backend Engineer", ENGLISH), token_budget=50)
    assert estimated_tokens(text) <= 50
