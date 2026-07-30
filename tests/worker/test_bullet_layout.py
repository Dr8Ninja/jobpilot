"""The tailored resume must have the same shape as the source resume.

Same sections, same roles, and — the part this file guards — the same number of
bullets per role, in the same order. Only the wording changes.

This is enforced in code rather than asked of the model, because the model was
measured doing the wrong thing on every single run: one live tailoring returned
1 bullet for a 5-bullet role, and the rendered PDF simply lost the other four.
The renderer now walks the *canonical* bullets and pulls in a rewrite where one
exists, so the layout cannot depend on the model behaving.
"""

import pytest
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from jobpilot_worker.fixtures import SAMPLE_FACTS
from jobpilot_worker.stages.render import bullets_for_render


@pytest.fixture
def facts() -> CanonicalFacts:
    return SAMPLE_FACTS


def _rewrite(index: int, original: str, rewritten: str) -> TailoredBullet:
    return TailoredBullet(
        employment_index=index, original=original, rewritten=rewritten, skills_referenced=[]
    )


def _output(bullets: list[TailoredBullet]) -> TailoringOutput:
    return TailoringOutput(
        summary="Backend engineer.", tailored_bullets=bullets, skills_ordered_for_this_jd=[]
    )


def test_every_role_keeps_its_original_bullet_count(facts: CanonicalFacts) -> None:
    """The regression the user reported: a short model reply truncated the resume."""
    # The model rewrote only the first bullet of the first role.
    first = facts.employment[0].bullets[0]
    rendered = bullets_for_render(facts, _output([_rewrite(0, first, "Rewritten first bullet.")]))

    for index, role in enumerate(facts.employment):
        assert len(rendered[index]) == len(role.bullets), (
            f"role {index} rendered {len(rendered[index])} bullets, source has {len(role.bullets)}"
        )


def test_a_bullet_the_model_ignored_falls_back_to_the_candidates_own_words(
    facts: CanonicalFacts,
) -> None:
    role = facts.employment[0]
    rendered = bullets_for_render(
        facts, _output([_rewrite(0, role.bullets[0], "Rewritten first bullet.")])
    )
    assert rendered[0][0] == "Rewritten first bullet."
    # Untouched positions keep the original text rather than vanishing.
    assert rendered[0][1:] == list(role.bullets[1:])


def test_rewrites_land_in_the_position_of_the_bullet_they_replace(
    facts: CanonicalFacts,
) -> None:
    """Order is part of the layout, so a rewrite must not migrate up the list."""
    role = facts.employment[0]
    rendered = bullets_for_render(
        facts, _output([_rewrite(0, role.bullets[1], "Second bullet, rewritten.")])
    )
    assert rendered[0][0] == role.bullets[0]
    assert rendered[0][1] == "Second bullet, rewritten."


def test_matching_survives_whitespace_and_case_differences(facts: CanonicalFacts) -> None:
    """Models routinely echo the original with cosmetic differences."""
    original = facts.employment[0].bullets[0]
    sloppy = f"  {original.upper()}  "
    rendered = bullets_for_render(facts, _output([_rewrite(0, sloppy, "Matched anyway.")]))
    assert rendered[0][0] == "Matched anyway."


def test_a_rewrite_whose_original_matches_nothing_still_lands_positionally(
    facts: CanonicalFacts,
) -> None:
    """A model that paraphrases the `original` field must not lose the rewrite."""
    rendered = bullets_for_render(
        facts, _output([_rewrite(0, "something the resume never said", "Useful rewrite.")])
    )
    assert rendered[0][0] == "Useful rewrite."
    assert len(rendered[0]) == len(facts.employment[0].bullets)


def test_extra_bullets_beyond_the_source_count_are_not_rendered(
    facts: CanonicalFacts,
) -> None:
    """An over-eager model must not grow the resume either — same shape both ways."""
    role = facts.employment[0]
    extras = [_rewrite(0, b, f"rewrite {i}") for i, b in enumerate(role.bullets)]
    extras += [_rewrite(0, "invented", "an invented sixth bullet")]
    rendered = bullets_for_render(facts, _output(extras))
    assert len(rendered[0]) == len(role.bullets)
    assert "an invented sixth bullet" not in rendered[0]


def test_an_out_of_range_employment_index_is_ignored(facts: CanonicalFacts) -> None:
    """Seen live: the model addressed a role that does not exist."""
    rendered = bullets_for_render(facts, _output([_rewrite(99, "x", "orphan rewrite")]))
    assert set(rendered) == set(range(len(facts.employment)))
    assert all("orphan rewrite" not in b for bullets in rendered.values() for b in bullets)


def test_no_rewrites_at_all_renders_the_source_resume(facts: CanonicalFacts) -> None:
    rendered = bullets_for_render(facts, _output([]))
    for index, role in enumerate(facts.employment):
        assert rendered[index] == list(role.bullets)


def test_an_empty_rewrite_does_not_blank_a_bullet(facts: CanonicalFacts) -> None:
    """A whitespace-only rewrite would leave a bullet marker with no text."""
    role = facts.employment[0]
    rendered = bullets_for_render(facts, _output([_rewrite(0, role.bullets[0], "   ")]))
    assert rendered[0][0] == role.bullets[0]


# ---------------------------------------------------------------------------
# The retry side: a short reply is a bad tailoring, not a success.
# ---------------------------------------------------------------------------


def test_missing_bullets_counts_the_shortfall_per_role(facts: CanonicalFacts) -> None:
    from jobpilot_worker.stages.tailor import missing_bullets

    first = facts.employment[0].bullets[0]
    shortfall = missing_bullets(facts, _output([_rewrite(0, first, "one rewrite")]))
    assert shortfall[0] == len(facts.employment[0].bullets) - 1
    for index, role in enumerate(facts.employment):
        if index != 0:
            assert shortfall[index] == len(role.bullets)


def test_a_complete_reply_reports_no_shortfall(facts: CanonicalFacts) -> None:
    from jobpilot_worker.stages.tailor import missing_bullets

    every = [
        _rewrite(i, bullet, f"rewrite {i}.{j}")
        for i, role in enumerate(facts.employment)
        for j, bullet in enumerate(role.bullets)
    ]
    assert missing_bullets(facts, _output(every)) == {}


def test_an_empty_reply_is_retried_rather_than_accepted(facts: CanonicalFacts) -> None:
    """Measured live: a fallback model returned zero bullets and still passed the
    fact-check, producing a correctly-shaped but completely untailored resume."""
    from jobpilot_worker.stages.tailor import tailor_job

    calls: list[str] = []
    complete = _output(
        [
            _rewrite(i, bullet, f"rewrite {i}.{j}")
            for i, role in enumerate(facts.employment)
            for j, bullet in enumerate(role.bullets)
        ]
    )
    replies = [_output([]), complete]

    class FakeClient:
        def parse(self, *, model, max_tokens, system, prompt, output_format):
            calls.append(prompt)
            return replies[len(calls) - 1]

    class FakeCompany:
        name = "Acme Corp"

    class FakeJob:
        id = 1
        title = "Backend Engineer"
        description = "Python and PostgreSQL."
        company = FakeCompany()

    attempt = tailor_job(facts, FakeJob(), [], FakeClient())
    assert len(calls) == 2, "an empty reply must trigger a retry"
    assert "incomplete" in attempt.history[0]
    # The retry prompt must say what was missing, or the model repeats itself.
    assert "needs" in calls[1] and "missing" in calls[1]
    assert attempt.passed
    assert attempt.output is complete


def test_the_most_complete_attempt_is_kept_not_the_last(facts: CanonicalFacts) -> None:
    """A later attempt can come back emptier than an earlier one."""
    from jobpilot_worker.stages.tailor import missing_bullets, tailor_job

    good = _output([_rewrite(0, facts.employment[0].bullets[0], "a useful rewrite")])
    replies = [good, _output([]), _output([])]

    class FakeClient:
        def __init__(self) -> None:
            self.n = 0

        def parse(self, **kwargs):
            reply = replies[min(self.n, len(replies) - 1)]
            self.n += 1
            return reply

    class FakeCompany:
        name = "Acme Corp"

    class FakeJob:
        id = 1
        title = "Backend Engineer"
        description = "Python."
        company = FakeCompany()

    attempt = tailor_job(facts, FakeJob(), [], FakeClient())
    assert attempt.output is good
    assert missing_bullets(facts, attempt.output)[0] == len(facts.employment[0].bullets) - 1
