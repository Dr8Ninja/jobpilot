"""canonical_facts is the source of truth — it must be immutable and strict."""

import pytest
from jobpilot_shared.canonical_facts import CanonicalFacts, Identity
from pydantic import ValidationError


def test_facts_are_frozen(facts: CanonicalFacts) -> None:
    with pytest.raises(ValidationError):
        facts.experience_years = 10  # type: ignore[misc]


def test_nested_models_are_frozen(facts: CanonicalFacts) -> None:
    with pytest.raises(ValidationError):
        facts.identity.name = "Someone Else"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    """A typo'd field must fail loudly rather than silently vanish."""
    with pytest.raises(ValidationError):
        Identity(name="A", email="a@example.invalid", yearsOfExperience=9)  # type: ignore[call-arg]


def test_employer_names_are_exposed_for_the_gate(facts: CanonicalFacts) -> None:
    assert facts.employer_names() == ("Acme Corp", "Beta Labs")


def test_round_trips_through_json(facts: CanonicalFacts) -> None:
    restored = CanonicalFacts.model_validate_json(facts.model_dump_json())
    assert restored == facts
