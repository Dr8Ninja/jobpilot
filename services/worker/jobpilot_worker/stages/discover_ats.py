"""Multi-provider ATS board discovery.

Every endpoint here is a company's own **documented, public, unauthenticated**
job-board JSON API — the same category as Greenhouse. No scraping, no HTML
parsing, no evasion (CLAUDE.md non-negotiable #1).

    Greenhouse      boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
    Lever           api.lever.co/v0/postings/{token}?mode=json
    Ashby           api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
    Workable        apply.workable.com/api/v1/widget/accounts/{token}?details=true
    SmartRecruiters api.smartrecruiters.com/v1/companies/{token}/postings

Adding a provider means adding one entry to `PROVIDERS`: a URL template and a
function turning its payload into `RawJob`s. Everything downstream — dedupe,
scoring, tailoring, the gate — is unchanged.

Per-board failure is isolated: one stale token is logged and skipped, never fatal.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..clients.http import BotCheckEncountered, fetch
from .types import RawJob, html_to_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoardResult:
    provider: str
    board_token: str
    jobs: list[RawJob]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _text(value: object) -> str:
    return html_to_text(value if isinstance(value, str) else "")


# --------------------------------------------------------------------------
# Per-provider payload adapters
# --------------------------------------------------------------------------


def _parse_greenhouse(payload: dict, token: str, company: str) -> list[RawJob]:
    jobs = []
    for entry in payload.get("jobs", []):
        jobs.append(
            RawJob(
                board_token=token,
                company_name=company,
                ats_job_id=str(entry["id"]),
                title=(entry.get("title") or "").strip(),
                location=(entry.get("location") or {}).get("name"),
                description=_text(entry.get("content")),
                apply_url=entry.get("absolute_url", ""),
                ats_provider="greenhouse",
            )
        )
    return jobs


def _parse_lever(payload: list, token: str, company: str) -> list[RawJob]:
    jobs = []
    for entry in payload:
        # Lever splits the JD across `description` plus a list of `lists`.
        body = [entry.get("descriptionPlain") or entry.get("description") or ""]
        for section in entry.get("lists", []) or []:
            body.append(f"\n{section.get('text', '')}\n{section.get('content', '')}")
        body.append(entry.get("additionalPlain") or entry.get("additional") or "")
        jobs.append(
            RawJob(
                board_token=token,
                company_name=company,
                ats_job_id=str(entry["id"]),
                title=(entry.get("text") or "").strip(),
                location=(entry.get("categories") or {}).get("location"),
                description=_text("".join(body)),
                apply_url=entry.get("hostedUrl") or entry.get("applyUrl", ""),
                ats_provider="lever",
            )
        )
    return jobs


def _parse_ashby(payload: dict, token: str, company: str) -> list[RawJob]:
    jobs = []
    for entry in payload.get("jobs", []):
        comp = entry.get("compensation") or {}
        summary = comp.get("compensationTierSummary") if isinstance(comp, dict) else None
        jobs.append(
            RawJob(
                board_token=token,
                company_name=company,
                ats_job_id=str(entry.get("id") or entry.get("jobId")),
                title=(entry.get("title") or "").strip(),
                location=entry.get("location"),
                description=_text(entry.get("descriptionHtml") or entry.get("descriptionPlain")),
                apply_url=entry.get("applyUrl") or entry.get("jobUrl", ""),
                salary=summary,
                ats_provider="ashby",
            )
        )
    return jobs


def _parse_workable(payload: dict, token: str, company: str) -> list[RawJob]:
    jobs = []
    for entry in payload.get("jobs", []):
        location = ", ".join(p for p in (entry.get("city"), entry.get("country")) if p) or (
            "Remote" if entry.get("telecommuting") else None
        )
        jobs.append(
            RawJob(
                board_token=token,
                company_name=company,
                ats_job_id=str(entry.get("shortcode") or entry.get("id")),
                title=(entry.get("title") or "").strip(),
                location=location,
                description=_text(
                    (entry.get("description") or "") + (entry.get("requirements") or "")
                ),
                apply_url=entry.get("application_url") or entry.get("url", ""),
                ats_provider="workable",
            )
        )
    return jobs


def _parse_smartrecruiters(payload: dict, token: str, company: str) -> list[RawJob]:
    jobs = []
    for entry in payload.get("content", []):
        loc = entry.get("location") or {}
        location = (
            ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
            or (loc.get("remote") and "Remote")
            or None
        )
        jobs.append(
            RawJob(
                board_token=token,
                company_name=company,
                ats_job_id=str(entry["id"]),
                title=(entry.get("name") or "").strip(),
                location=location,
                # The list endpoint omits the body; the JD arrives via the
                # per-posting endpoint, so these start thin and are enriched below.
                description=_text(
                    entry.get("jobAd", {})
                    .get("sections", {})
                    .get("jobDescription", {})
                    .get("text", "")
                ),
                apply_url=f"https://jobs.smartrecruiters.com/{token}/{entry['id']}",
                ats_provider="smartrecruiters",
            )
        )
    return jobs


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    parse: Callable[[object, str, str], list[RawJob]]


PROVIDERS: dict[str, Provider] = {
    "greenhouse": Provider(
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        _parse_greenhouse,  # type: ignore[arg-type]
    ),
    "lever": Provider(
        "lever",
        "https://api.lever.co/v0/postings/{token}?mode=json",
        _parse_lever,  # type: ignore[arg-type]
    ),
    "ashby": Provider(
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
        _parse_ashby,  # type: ignore[arg-type]
    ),
    "workable": Provider(
        "workable",
        "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
        _parse_workable,  # type: ignore[arg-type]
    ),
    "smartrecruiters": Provider(
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100",
        _parse_smartrecruiters,  # type: ignore[arg-type]
    ),
}


def discover_board(
    provider_name: str,
    board_token: str,
    company_name: str,
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> BoardResult:
    """Pull one company's board from one provider."""
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        return BoardResult(
            provider_name, board_token, [], error=f"unknown provider {provider_name}"
        )

    try:
        result = fetch_fn(provider.url.format(token=board_token), client=client)
        payload = result.json()
    except BotCheckEncountered as exc:
        log.warning("%s/%s handed off: %s", provider_name, board_token, exc)
        return BoardResult(provider_name, board_token, [], error=f"handoff: {exc.status}")
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        log.warning("%s/%s failed: %s", provider_name, board_token, exc)
        return BoardResult(provider_name, board_token, [], error=f"{type(exc).__name__}: {exc}")

    try:
        jobs = provider.parse(payload, board_token, company_name)
    except (KeyError, TypeError, AttributeError) as exc:
        log.warning("%s/%s payload shape unexpected: %s", provider_name, board_token, exc)
        return BoardResult(provider_name, board_token, [], error=f"parse: {exc}")

    return BoardResult(provider_name, board_token, [j for j in jobs if j.title])


def discover_boards(
    boards: list[tuple[str, str, str]],
    *,
    client: httpx.Client | None = None,
    fetch_fn=fetch,
) -> list[BoardResult]:
    """`boards` is a list of `(provider, board_token, company_name)`."""
    return [
        discover_board(provider, token, name, client=client, fetch_fn=fetch_fn)
        for provider, token, name in boards
    ]
