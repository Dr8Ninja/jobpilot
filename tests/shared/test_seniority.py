"""Seniority is now the *only* hard rejection reason.

The user's instruction was explicit: reject 8+ year roles — staff, principal and
above — and nothing else. So these tests pull in both directions. A false
positive here silently deletes a job the user wanted; a false negative only puts
one obviously-too-senior row in the queue. Both are covered, but the
false-positive cases are the ones that matter.
"""

import pytest
from jobpilot_shared.seniority import is_too_senior, required_years


@pytest.mark.parametrize(
    "title",
    [
        "Staff Software Engineer",
        "Senior Staff Engineer",
        "Principal Engineer",
        "Principal Software Engineer, Platform",
        "Distinguished Engineer",
        "Engineering Fellow",
        "Software Architect",
        "Director of Engineering",
        "VP of Engineering",
        "Vice President, Technology",
        "Head of Platform Engineering",
        "Chief Technology Officer",
        "Engineering Manager",
        "Senior Engineering Manager",
    ],
)
def test_titles_that_are_out_of_range(title: str) -> None:
    assert is_too_senior(title, "")


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer",
        "Software Engineer II",
        "Senior Software Engineer",
        "Backend Engineer",
        "Senior Backend Engineer",
        # Lead is commonly a 5-7 year title in India — the user wants to see it.
        "Lead Software Engineer",
        "Tech Lead",
        "Forward Deployed Engineer",
        "AI Engineer",
        "Machine Learning Engineer",
        # "Principal" here is the *company* name, not the level.
        "Software Engineer, Principal Financial Group",
    ],
)
def test_titles_that_stay_in_range(title: str) -> None:
    assert not is_too_senior(title, "")


def test_a_years_requirement_over_the_cap_is_rejected() -> None:
    assert is_too_senior("Software Engineer", "We require 10+ years of experience.")


def test_a_years_requirement_at_the_cap_is_kept() -> None:
    """8 is the boundary the user set, and boundaries are inclusive of the keep."""
    assert not is_too_senior("Software Engineer", "8+ years of experience required.")


def test_a_range_is_judged_on_its_lower_bound() -> None:
    """'5-10 years' will interview a 5-year candidate, so it stays."""
    assert not is_too_senior("Software Engineer", "5-10 years of professional experience")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3+ years of experience in backend development", 3),
        ("Minimum 5 years experience", 5),
        ("At least 2 years of relevant experience", 2),
        ("We want 4 to 6 years of experience", 4),
        ("8–12 years of experience", 8),
        ("Experience of 7 years in distributed systems", 7),
        # A number with nothing to do with seniority must not be read as one.
        ("We have been building payments for 12 years.", None),
        ("Founded 9 years ago, we serve 3 million users", None),
        ("", None),
    ],
)
def test_required_years_extraction(text: str, expected: int | None) -> None:
    assert required_years(text) == expected


def test_the_lowest_stated_requirement_wins() -> None:
    """JDs often state a headline number then a softer one; take the softer."""
    text = "10+ years of experience preferred. 4+ years of experience required."
    assert required_years(text) == 4


def test_no_stated_requirement_is_not_a_rejection() -> None:
    assert required_years("Come build with us.") is None
    assert not is_too_senior("Software Engineer", "Come build with us.")
