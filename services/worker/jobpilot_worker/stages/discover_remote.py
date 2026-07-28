"""Keyless remote/global job boards.

Documented public JSON APIs, no account and no key required, which is what makes
them a cheap way to widen coverage beyond India-centric sources:

    Remotive    remotive.com/api/remote-jobs
    Arbeitnow   arbeitnow.com/api/job-board-api      (EU-heavy)
    RemoteOK    remoteok.com/api                      (requires a real User-Agent)

These return full descriptions, unlike an aggregator snippet — so rows from here
are `description_quality='full'` and score on the same footing as an ATS row.

Deliberately **not** here: Naukri and Cutshort. Neither publishes a third-party
job-search API, so pulling listings from them would mean scraping their web UI,
which non-negotiable #1 forbids. See README for what to do instead.
"""

import logging
from dataclasses import dataclass

import httpx

from ..clients.http import BotCheckEncountered, fetch
from .types import RawListing, html_to_text, parse_timestamp

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteBoardResult:
    source: str
    listings: list[RawListing]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _salary(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_remotive(payload: dict) -> list[RawListing]:
    out = []
    for entry in payload.get("jobs", []):
        out.append(
            RawListing(
                external_id=str(entry["id"]),
                company_name=(entry.get("company_name") or "Unknown").strip(),
                title=(entry.get("title") or "").strip(),
                location=entry.get("candidate_required_location") or "Remote",
                snippet=html_to_text(entry.get("description", "")),
                redirect_url=entry.get("url", ""),
                salary=_salary(entry.get("salary")),
                posted_at=parse_timestamp(entry.get("publication_date")),
            )
        )
    return out


def _parse_arbeitnow(payload: dict) -> list[RawListing]:
    out = []
    for entry in payload.get("data", []):
        out.append(
            RawListing(
                external_id=str(entry.get("slug") or entry.get("id")),
                company_name=(entry.get("company_name") or "Unknown").strip(),
                title=(entry.get("title") or "").strip(),
                location=entry.get("location") or ("Remote" if entry.get("remote") else None),
                snippet=html_to_text(entry.get("description", "")),
                redirect_url=entry.get("url", ""),
                posted_at=parse_timestamp(entry.get("created_at")),
            )
        )
    return out


def _parse_remoteok(payload: list) -> list[RawListing]:
    out = []
    for entry in payload:
        # The first element is a legal/attribution notice, not a job.
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("position"):
            continue
        out.append(
            RawListing(
                external_id=str(entry["id"]),
                company_name=(entry.get("company") or "Unknown").strip(),
                title=(entry.get("position") or "").strip(),
                location=entry.get("location") or "Remote",
                snippet=html_to_text(entry.get("description", "")),
                redirect_url=entry.get("url") or entry.get("apply_url", ""),
                salary=_salary(
                    f"{entry.get('salary_min')}-{entry.get('salary_max')}"
                    if entry.get("salary_min")
                    else None
                ),
                posted_at=parse_timestamp(entry.get("date") or entry.get("epoch")),
            )
        )
    return out


BOARDS = {
    "remotive": (
        "https://remotive.com/api/remote-jobs?category=software-dev&limit={limit}",
        _parse_remotive,
    ),
    "arbeitnow": ("https://www.arbeitnow.com/api/job-board-api", _parse_arbeitnow),
    "remoteok": ("https://remoteok.com/api", _parse_remoteok),
}


def discover_remote_board(
    source: str,
    *,
    limit: int = 100,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> RemoteBoardResult:
    entry = BOARDS.get(source)
    if entry is None:
        return RemoteBoardResult(source, [], error=f"unknown remote board {source}")
    url_template, parse = entry

    try:
        result = fetch_fn(url_template.format(limit=limit), client=client)
        payload = result.json()
    except BotCheckEncountered as exc:
        log.warning("%s handed off: %s", source, exc)
        return RemoteBoardResult(source, [], error=f"handoff: {exc.status}")
    except (httpx.HTTPError, OSError, ValueError) as exc:
        log.warning("%s failed: %s", source, exc)
        return RemoteBoardResult(source, [], error=f"{type(exc).__name__}: {exc}")

    try:
        listings = [entry for entry in parse(payload) if entry.title]
    except (KeyError, TypeError, AttributeError) as exc:
        return RemoteBoardResult(source, [], error=f"parse: {exc}")

    return RemoteBoardResult(source, listings[:limit])
