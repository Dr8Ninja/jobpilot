"""Adversarial suite for the anti-hallucination gate.

This is the priority test suite in the project: it is the only thing standing
between the tailoring engine and a fabricated resume. Cases are written from the
attacker's side — what would a plausible-but-wrong LLM output look like?
"""

import pytest
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from jobpilot_shared.whitelist import (
    Rejected,
    check,
    format_violations_for_retry,
)


def _output(
    summary: str = "Software engineer focused on backend services.",
    rewritten: str = "Built REST endpoints for the billing service using Python.",
    skills_referenced: list[str] | None = None,
    ordered: list[str] | None = None,
    employment_index: int = 0,
) -> TailoringOutput:
    return TailoringOutput(
        summary=summary,
        tailored_bullets=[
            TailoredBullet(
                employment_index=employment_index,
                original="Built REST endpoints for the billing service.",
                rewritten=rewritten,
                skills_referenced=skills_referenced
                if skills_referenced is not None
                else ["Python"],
            )
        ],
        skills_ordered_for_this_jd=ordered if ordered is not None else ["Python"],
    )


def _rules(result) -> set[str]:
    return {v.rule for v in result.reasons}


# --------------------------------------------------------------------------
# The happy path must actually pass — a gate that rejects everything is useless.
# --------------------------------------------------------------------------


def test_faithful_output_passes(facts: CanonicalFacts) -> None:
    result = check(facts, _output())
    assert result.passed, getattr(result, "reasons", None)


def test_reordering_and_rephrasing_is_allowed(facts: CanonicalFacts) -> None:
    result = check(
        facts,
        _output(
            summary="Backend engineer with 1.5 years building Python services.",
            rewritten="Shipped REST endpoints powering billing, in Python and FastAPI.",
            skills_referenced=["Python", "FastAPI"],
            ordered=["Python", "FastAPI", "PostgreSQL"],
        ),
    )
    assert result.passed, getattr(result, "reasons", None)


# --------------------------------------------------------------------------
# Rule 1 — unknown_skill (reject)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    ["Node.js", "NodeJS", "node js", "NODE.JS", "  Node.js  "],
)
def test_skill_spelling_variants_are_accepted(facts: CanonicalFacts, variant: str) -> None:
    """Honest spelling variance must not burn a retry."""
    result = check(facts, _output(skills_referenced=[variant]))
    assert result.passed, getattr(result, "reasons", None)


def test_undeclared_skill_is_rejected(facts: CanonicalFacts) -> None:
    result = check(facts, _output(skills_referenced=["Python", "Kubernetes"]))
    assert isinstance(result, Rejected)
    assert "unknown_skill" in _rules(result)
    assert any("Kubernetes" in v.evidence for v in result.reasons)


def test_undeclared_skill_in_ordered_list_is_rejected(facts: CanonicalFacts) -> None:
    """The ordered list is rendered into the PDF, so it is gated just as hard."""
    result = check(facts, _output(ordered=["Python", "Go"]))
    assert isinstance(result, Rejected)
    assert "unknown_skill" in _rules(result)


def test_cpp_and_csharp_do_not_collide(facts: CanonicalFacts) -> None:
    """Naive punctuation stripping collapses C++ and C# to 'c'; both must reject."""
    for skill in ("C++", "C#"):
        result = check(facts, _output(skills_referenced=[skill]))
        assert isinstance(result, Rejected), f"{skill} should not pass"
        assert "unknown_skill" in _rules(result)


def test_homoglyph_skill_is_rejected(facts: CanonicalFacts) -> None:
    """'Pythоn' with a Cyrillic 'о' must not sneak through as canonical 'Python'."""
    sneaky = "Pythоn"  # CYRILLIC SMALL LETTER O
    assert sneaky != "Python"
    result = check(facts, _output(skills_referenced=[sneaky]))
    assert isinstance(result, Rejected)
    assert "unknown_skill" in _rules(result)
    assert "lookalike" in " ".join(v.detail for v in result.reasons)


# --------------------------------------------------------------------------
# Rule 2 — invalid_employment_index (reject)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("index", [-1, 2, 99])
def test_out_of_range_employment_index_is_rejected(facts: CanonicalFacts, index: int) -> None:
    result = check(facts, _output(employment_index=index))
    assert isinstance(result, Rejected)
    assert "invalid_employment_index" in _rules(result)


# --------------------------------------------------------------------------
# Rule 3 — yoe_inflation (reject)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "Engineer with 5 years of backend experience.",
        "5+ years building distributed systems.",
        "Over 3 years of Python.",
        "3.5 yrs of production experience.",
    ],
)
def test_inflated_years_are_rejected(facts: CanonicalFacts, claim: str) -> None:
    result = check(facts, _output(summary=claim))
    assert isinstance(result, Rejected)
    assert "yoe_inflation" in _rules(result)


@pytest.mark.parametrize("claim", ["1.5 years of experience.", "1 year of Python."])
def test_truthful_years_are_accepted(facts: CanonicalFacts, claim: str) -> None:
    result = check(facts, _output(summary=claim))
    assert result.passed, getattr(result, "reasons", None)


def test_inflated_years_inside_a_bullet_are_rejected(facts: CanonicalFacts) -> None:
    result = check(facts, _output(rewritten="Brought 4 years of Redis expertise."))
    assert isinstance(result, Rejected)
    assert "yoe_inflation" in _rules(result)


# --------------------------------------------------------------------------
# Rule 4 — unknown_employer (reject)
# --------------------------------------------------------------------------


def test_fabricated_employer_is_rejected(facts: CanonicalFacts) -> None:
    result = check(facts, _output(rewritten="Led the payments rewrite at Google."))
    assert isinstance(result, Rejected)
    assert "unknown_employer" in _rules(result)


def test_real_employer_is_accepted(facts: CanonicalFacts) -> None:
    result = check(facts, _output(rewritten="Shipped the billing service at Acme Corp."))
    assert result.passed, getattr(result, "reasons", None)


def test_real_employer_followed_by_more_text_is_accepted(facts: CanonicalFacts) -> None:
    result = check(
        facts,
        _output(rewritten="Worked at Beta Labs building internal tooling in Python."),
    )
    assert result.passed, getattr(result, "reasons", None)


def test_education_institution_is_accepted(facts: CanonicalFacts) -> None:
    result = check(
        facts,
        _output(rewritten="Studied at Example Institute of Technology before joining."),
    )
    assert result.passed, getattr(result, "reasons", None)


@pytest.mark.parametrize(
    "prose",
    [
        "Improved throughput at Scale.",
        "Owned the contract for API stability.",
        "Reduced errors for Q3.",
    ],
)
def test_common_capitalised_words_do_not_false_positive(facts: CanonicalFacts, prose: str) -> None:
    """A trigger-happy employer rule would reject honest output and loop forever."""
    result = check(facts, _output(rewritten=prose))
    assert result.passed, getattr(result, "reasons", None)


# --------------------------------------------------------------------------
# Rule 5 — unlisted_token (flag, not reject)
# --------------------------------------------------------------------------


def test_unlisted_technology_in_prose_is_flagged_not_rejected(
    facts: CanonicalFacts,
) -> None:
    """The model wrote 'Kubernetes' in prose while declaring only Docker."""
    result = check(
        facts,
        _output(
            rewritten="Containerised the service with Docker and deployed to Kubernetes.",
            skills_referenced=["Docker"],
        ),
    )
    assert result.passed, "prose tokens are flagged for review, not rejected (PRD §4.4)"
    assert any(w.rule == "unlisted_token" for w in result.warnings)
    assert any("Kubernetes" in w.evidence for w in result.warnings)


def test_homoglyph_evades_nothing_in_prose(facts: CanonicalFacts) -> None:
    """'Kubеrnetes' with a Cyrillic 'е' must still be caught by the lexicon scan."""
    sneaky = "Kubеrnetes"  # CYRILLIC SMALL LETTER IE
    assert sneaky != "Kubernetes"
    result = check(facts, _output(rewritten=f"Deployed to {sneaky} in production."))
    assert any(w.rule == "unlisted_token" for w in result.warnings)


def test_whitelisted_technology_in_prose_is_not_flagged(facts: CanonicalFacts) -> None:
    result = check(facts, _output(rewritten="Cached hot reads in Redis."))
    assert not result.warnings


def test_multiword_lexicon_entry_matches_longest(facts: CanonicalFacts) -> None:
    result = check(facts, _output(rewritten="Migrated the app to Ruby on Rails."))
    evidence = {w.evidence for w in result.warnings}
    assert "Ruby on Rails" in evidence
    assert "Ruby" not in evidence


# --------------------------------------------------------------------------
# Combination + retry surface
# --------------------------------------------------------------------------


def test_multiple_violations_are_all_reported(facts: CanonicalFacts) -> None:
    """One pass must surface every problem, so a retry can fix them together."""
    result = check(
        facts,
        _output(
            summary="Engineer with 6 years of experience.",
            rewritten="Ran the platform at Netflix.",
            skills_referenced=["Kubernetes"],
        ),
    )
    assert isinstance(result, Rejected)
    assert _rules(result) == {"unknown_skill", "yoe_inflation", "unknown_employer"}


def test_empty_canonical_skills_rejects_every_declared_skill(
    facts: CanonicalFacts,
) -> None:
    bare = facts.model_copy(update={"skills": ()})
    result = check(bare, _output(skills_referenced=["Python"]))
    assert isinstance(result, Rejected)
    assert "unknown_skill" in _rules(result)


def test_empty_output_passes(facts: CanonicalFacts) -> None:
    """Nothing claimed, nothing to reject."""
    result = check(facts, TailoringOutput(summary=""))
    assert result.passed


def test_retry_prompt_names_every_rule_and_evidence(facts: CanonicalFacts) -> None:
    result = check(facts, _output(skills_referenced=["Kubernetes"]))
    assert isinstance(result, Rejected)
    prompt = format_violations_for_retry(result.reasons)
    assert "unknown_skill" in prompt
    assert "Kubernetes" in prompt


# --------------------------------------------------------------------------
# target_company — naming the company you are applying to is not a claim
# --------------------------------------------------------------------------


def test_target_company_in_prose_is_rejected_without_context(facts: CanonicalFacts) -> None:
    """Default behaviour stays strict: an unknown org is a rejection."""
    result = check(facts, _output(summary="Backend engineer targeting a role at Gamma Systems."))
    assert isinstance(result, Rejected)
    assert "unknown_employer" in _rules(result)


def test_target_company_is_allowed_when_supplied(facts: CanonicalFacts) -> None:
    """Found by running the pipeline: honest summaries were being rejected.

    "Seeking a backend role at Gamma Systems" names the company being applied to,
    not one the candidate claims to have worked for.
    """
    result = check(
        facts,
        _output(summary="Backend engineer targeting a role at Gamma Systems."),
        target_company="Gamma Systems",
    )
    assert result.passed, getattr(result, "reasons", None)


def test_target_company_does_not_whitelist_other_employers(facts: CanonicalFacts) -> None:
    """The exemption is one company wide, not a hole in the rule."""
    result = check(
        facts,
        _output(rewritten="Led the payments rewrite at Google."),
        target_company="Gamma Systems",
    )
    assert isinstance(result, Rejected)
    assert "unknown_employer" in _rules(result)


# --------------------------------------------------------------------------
# The identity property: the user's own resume must always pass its own gate
# --------------------------------------------------------------------------


def test_verbatim_source_bullets_always_pass(facts: CanonicalFacts) -> None:
    """A tailoring that changes nothing must never be rejected.

    Found live: "built a training pipeline with IR_50" tripped the employer rule,
    which meant an unmodified copy of the candidate's own resume failed the
    fact-check. If the gate cannot pass the source document, it is miscalibrated.
    """
    output = TailoringOutput(
        summary="",
        tailored_bullets=[
            TailoredBullet(
                employment_index=index, original=bullet, rewritten=bullet, skills_referenced=[]
            )
            for index, role in enumerate(facts.employment)
            for bullet in role.bullets
        ],
        skills_ordered_for_this_jd=list(facts.skills),
    )
    result = check(facts, output)
    assert result.passed, getattr(result, "reasons", None)


def test_a_technology_the_user_wrote_about_is_claimable(facts: CanonicalFacts) -> None:
    """Prose provenance: naming something from your own bullets is not invention."""
    bullet = facts.employment[0].bullets[0]
    result = check(
        facts,
        _output(rewritten=bullet, skills_referenced=["Python"]),
    )
    assert result.passed, getattr(result, "reasons", None)
