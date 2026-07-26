"""Redirect resolution and Greenhouse URL parsing — the certainty dedupe rule.

The user's rule: an aggregator row is dropped **only** when we are certain it is
the same job, proven by a matching Greenhouse `(board_token, ats_job_id)`. No
fuzzy title or location matching happens anywhere in this module, and none should
be added — a near-miss must produce two rows, not one wrong one.

Following a redirect is a plain HTTP GET against a documented API's own link. If
the destination answers with a 403 or a bot check we record it and move on: the
row simply stays un-deduped (CLAUDE.md non-negotiable #1).
"""

import re
from urllib.parse import parse_qs, urlparse

import httpx

from ..clients.http import BotCheckEncountered, fetch
from .types import RawListing, ResolvedListing

#: https://boards.greenhouse.io/acme/jobs/4012345
#: https://job-boards.greenhouse.io/acme/jobs/4012345?gh_src=abc
_PATH_PATTERN = re.compile(
    r"^/(?:embed/job_board/?)?(?P<token>[A-Za-z0-9_-]+)/jobs/(?P<job_id>\d+)"
)
#: https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345
_EMBED_PATH = "/embed/job_app"

_GREENHOUSE_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
}


def parse_greenhouse_url(url: str) -> tuple[str, str] | None:
    """Extract `(board_token, ats_job_id)` from a Greenhouse job URL.

    Returns None for anything that is not unambiguously a Greenhouse job page —
    a careers-page landing URL, another ATS, or a shortened link. None means
    "not certain", which means the row is kept rather than dropped.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host not in _GREENHOUSE_HOSTS:
        return None

    if parsed.path.rstrip("/") == _EMBED_PATH:
        params = parse_qs(parsed.query)
        token = (params.get("for") or [""])[0]
        job_id = (params.get("token") or [""])[0]
        if token and job_id.isdigit():
            return token, job_id
        return None

    match = _PATH_PATTERN.match(parsed.path)
    if match:
        return match.group("token"), match.group("job_id")
    return None


def resolve_listing(
    listing: RawListing,
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> ResolvedListing:
    """Follow the aggregator's redirect and try to identify a Greenhouse job."""
    notes: list[str] = []

    # Some aggregators hand back the destination directly; try parsing first so a
    # network call is skipped when it buys nothing.
    direct = parse_greenhouse_url(listing.redirect_url)
    if direct:
        return ResolvedListing(
            listing=listing,
            resolved_url=listing.redirect_url,
            board_token=direct[0],
            ats_job_id=direct[1],
            notes=["destination was already a Greenhouse job URL"],
        )

    try:
        result = fetch_fn(listing.redirect_url, client=client)
    except BotCheckEncountered as exc:
        # Never retried around. The row stays un-deduped and thin.
        return ResolvedListing(
            listing=listing,
            notes=[f"handoff: {exc.marker or exc.status} at {exc.url}"],
        )
    except (httpx.HTTPError, OSError) as exc:
        return ResolvedListing(
            listing=listing, notes=[f"redirect resolution failed: {type(exc).__name__}"]
        )

    identity = parse_greenhouse_url(result.final_url)
    if identity is None:
        notes.append("destination is not a Greenhouse job URL; keeping as a separate row")
        return ResolvedListing(listing=listing, resolved_url=result.final_url, notes=notes)

    return ResolvedListing(
        listing=listing,
        resolved_url=result.final_url,
        board_token=identity[0],
        ats_job_id=identity[1],
        notes=notes,
    )
