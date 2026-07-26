"""Adzuna aggregator discovery.

Documented REST API with an India-inclusive index. Two roles in Phase 0, at the
user's direction: it grows the company registry *and* supplies job rows directly
for companies with no resolvable Greenhouse board.

Rows from here are snippets, not full JDs. `resolve.py` upgrades what it can and
everything else is marked `description_quality='thin'` so the review queue can
badge it and tailoring quality stays separable.
"""

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from jobpilot_shared.settings import get_settings

from ..clients.http import BotCheckEncountered, fetch
from .types import RawListing

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


@dataclass
class AggregatorResult:
    listings: list[RawListing]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _salary(entry: dict) -> str | None:
    low, high = entry.get("salary_min"), entry.get("salary_max")
    if not low and not high:
        return None
    if low and high and low != high:
        return f"{int(low):,}–{int(high):,}"
    return f"{int(low or high):,}"


def search(
    what: str,
    *,
    where: str = "",
    country: str = "in",
    page: int = 1,
    results_per_page: int = 50,
    max_days_old: int = 21,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> AggregatorResult:
    settings = get_settings()
    if not settings.fixture_mode and (not settings.adzuna_app_id or not settings.adzuna_app_key):
        return AggregatorResult([], error="ADZUNA_APP_ID / ADZUNA_APP_KEY not configured")

    query = urlencode(
        {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": results_per_page,
            "what": what,
            "where": where,
            "max_days_old": max_days_old,
            "content-type": "application/json",
        }
    )
    url = f"{SEARCH_URL.format(country=country, page=page)}?{query}"

    try:
        payload = fetch_fn(url, client=client).json()
    except BotCheckEncountered as exc:
        log.warning("Aggregator handed off: %s", exc)
        return AggregatorResult([], error=f"handoff: {exc.status}")
    except (httpx.HTTPError, OSError, ValueError) as exc:
        log.warning("Aggregator search failed: %s", exc)
        return AggregatorResult([], error=f"{type(exc).__name__}: {exc}")

    listings: list[RawListing] = []
    for entry in payload.get("results", []) if isinstance(payload, dict) else []:
        try:
            listings.append(
                RawListing(
                    external_id=str(entry["id"]),
                    company_name=(entry.get("company") or {}).get("display_name", "").strip()
                    or "Unknown",
                    title=entry.get("title", "").strip(),
                    location=(entry.get("location") or {}).get("display_name"),
                    snippet=(entry.get("description") or "").strip(),
                    redirect_url=entry.get("redirect_url", ""),
                    salary=_salary(entry),
                )
            )
        except (KeyError, TypeError) as exc:
            log.warning("Skipping malformed aggregator result: %s", exc)
    return AggregatorResult(listings)
