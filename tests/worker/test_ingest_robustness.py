"""A provider must never be able to abort a discovery run.

Discovery pulls tens of thousands of rows from nine providers that each shape
their payloads differently. Three separate runs have now been lost to a single
bad row, so both halves of the defence are tested here: ids are bounded before
they reach the database, and a row-level failure that still gets through is
absorbed rather than propagated.
"""

import pytest
from jobpilot_shared.db.models import Company, Job
from jobpilot_worker.stages import ingest
from jobpilot_worker.stages.types import EXTERNAL_ID_LIMIT, RawListing, bound_external_id

# ---------------------------------------------------------------------------
# Bounding the id
# ---------------------------------------------------------------------------


def test_a_short_id_is_left_exactly_as_the_provider_wrote_it() -> None:
    assert bound_external_id("gh-4012345") == "gh-4012345"


def test_a_long_id_is_brought_within_the_column_width() -> None:
    """The real one: Arbeitnow's slug carries every location a posting names."""
    slug = (
        "senior-staff-software-engineer-agentic-platform-berlin-bengaluru-india-"
        "delhi-ncr-india-hyderabad-lithuania-serbia-united-kingdom-119533"
    ) * 6
    bounded = bound_external_id(slug)
    assert len(bounded) <= EXTERNAL_ID_LIMIT
    assert bounded.startswith("senior-staff-software-engineer")


def test_bounding_is_deterministic_so_dedupe_still_works() -> None:
    """A re-run must map the same posting to the same row, not insert a new one."""
    slug = "a" * 900
    assert bound_external_id(slug) == bound_external_id(slug)


def test_two_long_ids_sharing_a_prefix_stay_distinct() -> None:
    """Plain truncation would merge these two into one job. The digest prevents it."""
    shared = "acme-senior-backend-engineer-" + "x" * 800
    assert bound_external_id(shared + "-alpha") != bound_external_id(shared + "-beta")


# ---------------------------------------------------------------------------
# Absorbing a row that still fails
# ---------------------------------------------------------------------------


@pytest.fixture
def company(db):
    row = Company(name="Acme Corp", normalized_name="acme", discovered_via="seed")
    db.add(row)
    db.flush()
    return row


def _listing(external_id: str) -> RawListing:
    return RawListing(
        external_id=external_id,
        company_name="Acme Corp",
        title="Backend Engineer",
        location="Bengaluru, India",
        snippet="Python and PostgreSQL.",
        redirect_url="https://example.invalid/apply",
    )


def test_an_over_long_slug_now_lands_instead_of_aborting(db, company) -> None:
    listing = _listing("x" * 900)
    outcome = ingest.ingest_one(db, ingest.ingest_remote_listing, "arbeitnow", listing)
    assert outcome.inserted == 1
    assert db.query(Job).count() == 1


def test_a_failing_row_is_skipped_and_the_next_one_still_lands(db, company) -> None:
    """The property that matters: one bad listing costs one listing, not the run."""

    def explode(session, *args):
        raise ingest.DataError("INSERT ...", {}, Exception("value too long"))

    bad = ingest.ingest_one(db, explode, "arbeitnow", _listing("bad"))
    assert bad.inserted == 0
    assert bad.failed == 1
    assert "ingest failed on one listing" in bad.notes[0]
    # Not `skipped`: that already means "we already had this row", and a healthy
    # re-run reports thousands of those.
    assert bad.skipped == 0

    good = ingest.ingest_one(db, ingest.ingest_remote_listing, "arbeitnow", _listing("good"))
    assert good.inserted == 1, "the session must be usable after a rolled-back failure"
