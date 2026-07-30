"""What the daily run tailors.

The rule the user set: never drop a job for missing skills — tailoring exists to
close that gap, and the gaps themselves are the study list. Drop only 8+ year
roles, and spend the daily budget on India and remote. Everything else that was
scored stays visible in the shortlist tab.

These use lightweight stand-ins rather than database rows: selection reads five
attributes and nothing else, so a real session would only slow the test down.
"""

from dataclasses import dataclass, field

import pytest
from jobpilot_worker.stages.score import select_for_tailoring


@dataclass
class FakeJob:
    id: int
    title: str = "Software Engineer"
    description: str = "Build backend services."
    location_kind: str = "india"


@dataclass
class FakeScore:
    job_id: int
    match_score: int = 80
    verdict: dict = field(default_factory=dict)


def _pair(job_id: int, **kwargs):
    """One (score, job) pair. Score kwargs win over job kwargs by name."""
    score_keys = {"match_score", "verdict"}
    score = FakeScore(job_id, **{k: v for k, v in kwargs.items() if k in score_keys})
    job = FakeJob(job_id, **{k: v for k, v in kwargs.items() if k not in score_keys})
    return score, job


def _select(pairs):
    scores = [s for s, _ in pairs]
    jobs = {j.id: j for _, j in pairs}
    return [row.job_id for row in select_for_tailoring(scores, jobs)]


def test_a_skills_gap_never_drops_a_job() -> None:
    """The whole point of the change: a weak band is ranked down, not deleted."""
    pairs = [
        _pair(1, match_score=38, verdict={"fit_band": "weak", "keyword_gaps": ["Kubernetes"]}),
        _pair(2, match_score=92, verdict={"fit_band": "excellent"}),
    ]
    selected = _select(pairs)
    assert selected == [2, 1], "the weaker match must still be tailored, just later"


def test_an_eight_plus_year_role_is_dropped() -> None:
    pairs = [
        _pair(1, description="We need 12+ years of experience."),
        _pair(2, description="2+ years of experience."),
    ]
    assert _select(pairs) == [2]


@pytest.mark.parametrize(
    "title", ["Staff Engineer", "Principal Engineer", "Director of Engineering"]
)
def test_staff_and_above_titles_are_dropped(title: str) -> None:
    assert _select([_pair(1, title=title)]) == []


def test_the_models_own_seniority_mismatch_is_honoured() -> None:
    assert _select([_pair(1, verdict={"seniority_fit": "mismatch"})]) == []


def test_a_stretch_role_is_kept() -> None:
    """The user asked to see roles above their years, not only at them."""
    assert _select([_pair(1, verdict={"seniority_fit": "stretch"})]) == [1]


def test_overseas_roles_do_not_spend_the_daily_budget() -> None:
    pairs = [_pair(1, location_kind="overseas"), _pair(2, location_kind="remote")]
    assert _select(pairs) == [2]


def test_unknown_location_is_treated_as_overseas() -> None:
    """Conservative by design — an unplaceable role is not promoted."""
    assert _select([_pair(1, location_kind="unknown")]) == []


def test_india_outranks_a_better_scoring_remote_role() -> None:
    pairs = [
        _pair(1, location_kind="remote", match_score=95),
        _pair(2, location_kind="india", match_score=70),
    ]
    assert _select(pairs) == [2, 1]


def test_the_models_should_apply_false_does_not_veto() -> None:
    """Observed live: "skip" on an 88. The code decides, not the model."""
    assert _select([_pair(1, verdict={"should_apply": False})]) == [1]


def test_the_daily_cap_bounds_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Volume stays a bounded dial (CLAUDE.md non-negotiable #4)."""
    from jobpilot_shared.settings import get_settings

    monkeypatch.setenv("JOBPILOT_MAX_TAILORED_PER_DAY", "3")
    get_settings(refresh=True)
    try:
        pairs = [_pair(i, match_score=90 - i) for i in range(1, 11)]
        assert len(_select(pairs)) == 3
    finally:
        # The singleton is process-wide; leaving it at 3 would poison later tests.
        monkeypatch.delenv("JOBPILOT_MAX_TAILORED_PER_DAY", raising=False)
        get_settings(refresh=True)


def test_missing_job_rows_do_not_crash_selection() -> None:
    """A job deleted between scoring and selection must not take the run down."""
    scores = [FakeScore(1)]
    assert select_for_tailoring(scores, {}) == []
