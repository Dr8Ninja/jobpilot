"""Location classification decides what reaches the main queue vs the overseas tab.

The asymmetry matters: mislabelling an overseas role as remote puts a job the
candidate cannot take at the top of the queue, while mislabelling a remote role
as overseas only moves it one tab across. So unknown defaults to overseas.
"""

import pytest
from jobpilot_shared.location import classify_location, is_preferred


@pytest.mark.parametrize(
    "location",
    [
        "Bengaluru, India",
        "Bangalore",
        "Gurugram",
        "Hyderabad, Telangana",
        "Pune, Maharashtra",
        "Noida",
        "India",
        "Mohali, Punjab",
        # India beats the remote marker — it is still an India role.
        "Remote - India",
        "Remote (India)",
    ],
)
def test_india_locations(location: str) -> None:
    assert classify_location(location) == "india"


@pytest.mark.parametrize(
    "location",
    ["Remote", "Anywhere", "Worldwide", "Remote - Global", "Work from home"],
)
def test_open_remote_locations(location: str) -> None:
    assert classify_location(location) == "remote"


@pytest.mark.parametrize(
    "location",
    [
        # "Remote" that is really "remote inside another country" is not open
        # to someone in India, so it must not sit in the main queue.
        "Remote - US",
        "Remote, United States",
        "Remote (EU)",
        "Remote - Canada",
        "Remote — Germany",
        "US Remote",
    ],
)
def test_geographically_restricted_remote_is_overseas(location: str) -> None:
    assert classify_location(location) == "overseas"


@pytest.mark.parametrize(
    "location",
    ["Mountain View, California, USA", "London, UK", "Berlin", "The Netherlands", "Singapore"],
)
def test_overseas_locations(location: str) -> None:
    assert classify_location(location) == "overseas"


def test_empty_location_falls_back_to_the_description() -> None:
    assert classify_location("", description="This role is based in Bengaluru.") == "india"
    assert classify_location(None, description="Fully remote team.") == "remote"


def test_empty_and_uninformative_is_unknown_not_preferred() -> None:
    kind = classify_location("", description="We build great software.")
    assert kind == "unknown"
    assert not is_preferred(kind)


def test_preference_covers_india_and_remote_only() -> None:
    assert is_preferred("india")
    assert is_preferred("remote")
    assert not is_preferred("overseas")
    assert not is_preferred("unknown")


@pytest.mark.parametrize(
    "location",
    [
        # Found live: these all reached the India/remote queue before the
        # foreign-place check replaced the two-word-order regex.
        "Canada (Remote)",
        "United States (Remote)",
        "Republic of Ireland (Remote)",
        "Sweden (Remote)",
        "Remote, Poland",
        "Remote | Singapore",
        "EMEA - Remote",
        "Remote (LATAM)",
    ],
)
def test_a_country_anywhere_in_the_string_makes_it_overseas(location: str) -> None:
    assert classify_location(location) == "overseas"


def test_india_still_wins_over_a_foreign_word_in_the_same_string() -> None:
    """A global company's India posting must not be read as overseas."""
    assert classify_location("Bengaluru, India (US hours)") == "india"
    assert classify_location("Remote - India") == "india"


@pytest.mark.parametrize(
    "location",
    [
        # All observed live in the queue after the first fix.
        "Remote - California",
        "Remote: New York City",
        "Remote - MA",
        "Remote - Georgia; Remote - Texas",
        "Remote - SF Bay Area",
        "Remote, United Arab Emirates",
        "Remote - Bermuda",
        "Remote - Ontario",
    ],
)
def test_a_remote_role_pinned_to_a_state_or_metro_is_overseas(location: str) -> None:
    assert classify_location(location) == "overseas"


@pytest.mark.parametrize(
    "location",
    [
        # The inverted rule must not swallow genuinely open remote roles: these
        # contain no place qualifier at all.
        "Remote - anywhere in the world",
        "Remote or hybrid",
        "Fully remote, work from home",
        "Remote (global)",
        "Distributed",
        # Boards append nouns to the field; they name no place.
        "Remote job",
        "Remote position",
        "Remote only",
    ],
)
def test_an_unqualified_remote_role_stays_in_the_main_queue(location: str) -> None:
    assert classify_location(location) == "remote"


@pytest.mark.parametrize(
    "location",
    [
        # Bare city names, with no country anywhere in the string. Enumerating
        # these is hopeless, which is why the rule is inverted.
        "Remote - Austin",
        "Remote - Chicago",
        "Remote - Brussels",
        "Remote - Calgary",
        "Düsseldorf und Remote",
        "New-York, Atlanta, Remote, Toronto",
        "Remote  (Western States)",
    ],
)
def test_a_bare_city_qualifier_is_still_overseas(location: str) -> None:
    assert classify_location(location) == "overseas"


@pytest.mark.parametrize("location", ["Remote - IN", "Remote - OR", "Remote, ME"])
def test_a_state_code_that_lowercases_into_a_filler_word(location: str) -> None:
    """IN, OR and ME are Indiana, Oregon and Maine — not English words here."""
    assert classify_location(location) == "overseas"
