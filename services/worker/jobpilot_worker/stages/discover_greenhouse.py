"""Greenhouse board API discovery.

Documented, public, unauthenticated:
    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

One bad board token never aborts a run — each board is isolated and its failure
recorded, so the rest of the night's discovery still lands.
"""

import logging
from dataclasses import dataclass

import httpx

from ..clients.http import BotCheckEncountered, fetch
from .types import RawJob, html_to_text

log = logging.getLogger(__name__)

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?content=true"


@dataclass
class BoardResult:
    board_token: str
    jobs: list[RawJob]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _job_from_payload(token: str, company_name: str, payload: dict) -> RawJob:
    location = (payload.get("location") or {}).get("name")
    return RawJob(
        board_token=token,
        company_name=company_name,
        ats_job_id=str(payload["id"]),
        title=payload.get("title", "").strip(),
        location=location,
        description=html_to_text(payload.get("content", "")),
        apply_url=payload.get("absolute_url", ""),
    )


def discover_board(
    board_token: str,
    company_name: str,
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> BoardResult:
    """Pull every posting on one Greenhouse board."""
    try:
        result = fetch_fn(BOARD_URL.format(token=board_token), client=client)
        payload = result.json()
    except BotCheckEncountered as exc:
        log.warning("Greenhouse board %s handed off: %s", board_token, exc)
        return BoardResult(board_token, [], error=f"handoff: {exc.status}")
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        log.warning("Greenhouse board %s failed: %s", board_token, exc)
        return BoardResult(board_token, [], error=f"{type(exc).__name__}: {exc}")

    jobs: list[RawJob] = []
    for entry in payload.get("jobs", []) if isinstance(payload, dict) else []:
        try:
            jobs.append(_job_from_payload(board_token, company_name, entry))
        except (KeyError, TypeError) as exc:  # one malformed posting, not the board
            log.warning("Skipping malformed posting on %s: %s", board_token, exc)
    return BoardResult(board_token, jobs)


def discover_boards(
    boards: list[tuple[str, str]],
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> list[BoardResult]:
    """Pull many boards. `boards` is a list of `(board_token, company_name)`."""
    return [discover_board(token, name, client=client, fetch_fn=fetch_fn) for token, name in boards]


def fetch_single_job(
    board_token: str,
    ats_job_id: str,
    company_name: str,
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> RawJob | None:
    """Fetch one posting — used to upgrade a thin aggregator row to a full JD."""
    try:
        result = fetch_fn(JOB_URL.format(token=board_token, job_id=ats_job_id), client=client)
        return _job_from_payload(board_token, company_name, result.json())
    except (BotCheckEncountered, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        log.info("Could not upgrade %s/%s: %s", board_token, ats_job_id, exc)
        return None
