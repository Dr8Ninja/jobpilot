"""Shared HTTP behaviour for discovery.

Encodes CLAUDE.md non-negotiable #1 in one place: transient failures back off and
retry, but a 403 or a bot-check is **recorded and skipped, never retried around**.
There is no user-agent rotation, no proxy, no retry-until-it-works loop here, and
none may be added.
"""

import time
from dataclasses import dataclass

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
#: Statuses that mean "a human should look at this", not "try again differently".
HANDOFF_STATUS = frozenset({401, 403, 405, 407, 451})

#: We identify ourselves honestly. This is not fingerprint spoofing — several
#: public APIs (RemoteOK) reject a blank UA, and a truthful one is the correct fix.
USER_AGENT = "JobPilot/0.1 (personal job-search assistant; +https://github.com/Dr8Ninja)"

_BOT_CHECK_MARKERS = (
    "captcha",
    "are you a robot",
    "cf-challenge",
    "checking your browser",
    "attention required",
    "access denied",
)


class BotCheckEncountered(RuntimeError):
    """The endpoint asked for proof we are human. We stop and hand off."""

    def __init__(self, url: str, status: int, marker: str = "") -> None:
        self.url = url
        self.status = status
        self.marker = marker
        super().__init__(
            f"Bot check or access denial at {url} (status {status}"
            f"{f', marker {marker!r}' if marker else ''}). "
            "Handing off to the human — this tool never evades bot detection."
        )


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    text: str
    final_url: str

    def json(self) -> object:
        import json

        return json.loads(self.text)


def looks_like_bot_check(body: str) -> str:
    lowered = body[:4000].lower()
    for marker in _BOT_CHECK_MARKERS:
        if marker in lowered:
            return marker
    return ""


def fetch(
    url: str,
    *,
    client: httpx.Client | None = None,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    timeout: float = 30.0,
    follow_redirects: bool = True,
    sleep=time.sleep,
) -> FetchResult:
    """GET a documented public endpoint.

    Raises `BotCheckEncountered` on a handoff status or a bot-check body, and
    `httpx.HTTPStatusError` on a non-retryable error. Retries only on transient
    network and 5xx/429 conditions.
    """
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=follow_redirects,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.get(
                    url,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                    headers={"User-Agent": USER_AGENT},
                )
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == max_attempts:
                    raise
                sleep(base_delay * 2 ** (attempt - 1))
                continue

            if response.status_code in HANDOFF_STATUS:
                raise BotCheckEncountered(url, response.status_code)

            marker = looks_like_bot_check(response.text)
            if marker:
                raise BotCheckEncountered(url, response.status_code, marker)

            if response.status_code in RETRYABLE_STATUS:
                if attempt == max_attempts:
                    response.raise_for_status()
                sleep(base_delay * 2 ** (attempt - 1))
                continue

            response.raise_for_status()
            return FetchResult(
                url=url,
                status=response.status_code,
                text=response.text,
                final_url=str(response.url),
            )
        raise last_exc or RuntimeError(f"Failed to fetch {url}")
    finally:
        if owns_client:
            client.close()
