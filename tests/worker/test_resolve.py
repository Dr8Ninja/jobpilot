"""The certainty dedupe rule lives or dies here.

`parse_greenhouse_url` returning None means "not certain", which means the
aggregator row is kept. Every false positive in this function is a real job
silently dropped from the queue, so the negative cases matter more than the
positive ones.
"""

import httpx
import pytest
from jobpilot_worker.clients.http import BotCheckEncountered, FetchResult
from jobpilot_worker.stages.resolve import parse_greenhouse_url, resolve_listing
from jobpilot_worker.stages.types import RawListing


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/jobs/4012345", ("acme", "4012345")),
        ("https://job-boards.greenhouse.io/acme/jobs/4012345", ("acme", "4012345")),
        ("https://boards.greenhouse.io/acme/jobs/4012345?gh_src=abc", ("acme", "4012345")),
        ("https://boards.greenhouse.io/acme/jobs/4012345#apply", ("acme", "4012345")),
        ("https://boards.eu.greenhouse.io/acme/jobs/4012345", ("acme", "4012345")),
        ("https://BOARDS.GREENHOUSE.IO/acme/jobs/4012345", ("acme", "4012345")),
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345",
            ("acme", "4012345"),
        ),
        ("https://boards.greenhouse.io/acme-labs_2/jobs/999", ("acme-labs_2", "999")),
    ],
)
def test_greenhouse_job_urls_yield_identity(url: str, expected: tuple[str, str]) -> None:
    assert parse_greenhouse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        # A board landing page names no specific job.
        "https://boards.greenhouse.io/acme",
        "https://boards.greenhouse.io/acme/jobs/",
        # Non-numeric job id is not a Greenhouse job id.
        "https://boards.greenhouse.io/acme/jobs/abc",
        # A different ATS entirely.
        "https://jobs.lever.co/acme/1234",
        "https://acme.ashbyhq.com/acme/1234",
        # Lookalike host — must not be trusted.
        "https://boards.greenhouse.io.evil.example/acme/jobs/4012345",
        "https://greenhouse.io/acme/jobs/4012345",
        # The company's own careers page, even if it embeds Greenhouse.
        "https://acme.com/careers/backend-engineer",
        # Embed form missing half its identity.
        "https://boards.greenhouse.io/embed/job_app?for=acme",
        "https://boards.greenhouse.io/embed/job_app?token=4012345",
    ],
)
def test_ambiguous_urls_yield_no_identity(url: str) -> None:
    """None means 'keep both rows'. Guessing here loses real jobs."""
    assert parse_greenhouse_url(url) is None


def _listing(redirect_url: str) -> RawListing:
    return RawListing(
        external_id="4900000001",
        company_name="Acme Corp",
        title="Software Engineer, Backend",
        location="Bengaluru, India",
        snippet="We are hiring a backend engineer…",
        redirect_url=redirect_url,
    )


def test_direct_greenhouse_url_skips_the_network() -> None:
    def _fail(*args, **kwargs):
        raise AssertionError("should not have made a request")

    resolved = resolve_listing(
        _listing("https://boards.greenhouse.io/acme/jobs/4012345"), fetch_fn=_fail
    )
    assert resolved.is_certain_greenhouse_job
    assert (resolved.board_token, resolved.ats_job_id) == ("acme", "4012345")


def test_redirect_resolving_to_greenhouse_is_certain() -> None:
    def _fetch(url, **kwargs):
        return FetchResult(
            url=url,
            status=200,
            text="<html></html>",
            final_url="https://boards.greenhouse.io/acme/jobs/4012345",
        )

    resolved = resolve_listing(_listing("https://adzuna.example/land/1"), fetch_fn=_fetch)
    assert resolved.is_certain_greenhouse_job
    assert resolved.resolved_url.endswith("/jobs/4012345")


def test_redirect_to_a_careers_page_is_not_certain() -> None:
    def _fetch(url, **kwargs):
        return FetchResult(
            url=url,
            status=200,
            text="<html></html>",
            final_url="https://acme.com/careers/backend-engineer",
        )

    resolved = resolve_listing(_listing("https://adzuna.example/land/1"), fetch_fn=_fetch)
    assert not resolved.is_certain_greenhouse_job
    assert resolved.ats_job_id is None
    assert resolved.description_quality == "thin"


def test_bot_check_hands_off_and_leaves_the_row_undeduped() -> None:
    """Non-negotiable #1: we record and skip. We never retry around a bot check."""
    attempts = []

    def _fetch(url, **kwargs):
        attempts.append(url)
        raise BotCheckEncountered(url, 403, "captcha")

    resolved = resolve_listing(_listing("https://adzuna.example/land/1"), fetch_fn=_fetch)
    assert len(attempts) == 1, "a bot check must not be retried"
    assert not resolved.is_certain_greenhouse_job
    assert any("handoff" in note for note in resolved.notes)


def test_network_failure_leaves_the_row_undeduped() -> None:
    def _fetch(url, **kwargs):
        raise httpx.ConnectError("boom")

    resolved = resolve_listing(_listing("https://adzuna.example/land/1"), fetch_fn=_fetch)
    assert not resolved.is_certain_greenhouse_job
    assert any("failed" in note for note in resolved.notes)
