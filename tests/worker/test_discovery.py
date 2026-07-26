"""Discovery reads documented APIs, isolates failures, and never evades bot checks."""

import json
import pathlib

import httpx
import pytest
from jobpilot_worker.clients.http import BotCheckEncountered, FetchResult, fetch
from jobpilot_worker.stages.discover_greenhouse import discover_board, discover_boards
from jobpilot_worker.stages.types import html_to_text

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _board_fetch(url, **kwargs):
    return FetchResult(
        url=url,
        status=200,
        text=(FIXTURES / "greenhouse_board.json").read_text(),
        final_url=url,
    )


def test_board_postings_are_parsed() -> None:
    result = discover_board("acme", "Acme Corp", fetch_fn=_board_fetch)
    assert result.ok
    assert [j.ats_job_id for j in result.jobs] == ["4012345", "4012346", "4012347"]

    first = result.jobs[0]
    assert first.title == "Software Engineer, Backend"
    assert first.location == "Bengaluru, India"
    assert first.board_token == "acme"
    assert first.apply_url == "https://boards.greenhouse.io/acme/jobs/4012345"


def test_escaped_html_description_becomes_readable_text() -> None:
    result = discover_board("acme", "Acme Corp", fetch_fn=_board_fetch)
    description = result.jobs[0].description
    assert "<p>" not in description and "&lt;" not in description
    assert "Python and PostgreSQL" in description


def test_content_hash_is_stable_and_content_sensitive() -> None:
    a = discover_board("acme", "Acme Corp", fetch_fn=_board_fetch).jobs[0]
    b = discover_board("acme", "Acme Corp", fetch_fn=_board_fetch).jobs[0]
    assert a.hash == b.hash
    assert a.hash != discover_board("acme", "Acme Corp", fetch_fn=_board_fetch).jobs[1].hash


def test_one_bad_board_does_not_abort_the_run() -> None:
    """A single stale token must not cost the whole night's discovery."""

    def _fetch(url, **kwargs):
        if "broken" in url:
            raise httpx.HTTPStatusError("404", request=None, response=None)  # type: ignore[arg-type]
        return _board_fetch(url, **kwargs)

    results = discover_boards([("broken", "Broken Co"), ("acme", "Acme Corp")], fetch_fn=_fetch)
    assert not results[0].ok and results[0].jobs == []
    assert results[1].ok and len(results[1].jobs) == 3


def test_bot_check_is_recorded_not_evaded() -> None:
    calls = []

    def _fetch(url, **kwargs):
        calls.append(url)
        raise BotCheckEncountered(url, 403)

    result = discover_board("acme", "Acme Corp", fetch_fn=_fetch)
    assert len(calls) == 1, "must not retry around a bot check"
    assert not result.ok
    assert "handoff" in result.error


def test_malformed_posting_is_skipped_not_fatal() -> None:
    payload = json.dumps({"jobs": [{"no_id": True}, {"id": 1, "title": "Fine"}]})

    def _fetch(url, **kwargs):
        return FetchResult(url=url, status=200, text=payload, final_url=url)

    result = discover_board("acme", "Acme Corp", fetch_fn=_fetch)
    assert result.ok
    assert [j.ats_job_id for j in result.jobs] == ["1"]


# --------------------------------------------------------------------------
# HTTP policy
# --------------------------------------------------------------------------


def test_transient_errors_back_off_and_retry() -> None:
    attempts = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="try later")
        return httpx.Response(200, text="{}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch("https://example.invalid/x", client=client, sleep=slept.append)
    assert result.status == 200
    assert attempts["n"] == 3
    assert slept == [0.5, 1.0], "exponential backoff"


@pytest.mark.parametrize("status", [401, 403, 451])
def test_handoff_statuses_raise_immediately(status: int) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(status, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BotCheckEncountered):
        fetch("https://example.invalid/x", client=client, sleep=lambda _: None)
    assert attempts["n"] == 1


def test_captcha_body_on_a_200_is_still_a_handoff() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Please complete the CAPTCHA to continue</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BotCheckEncountered) as exc:
        fetch("https://example.invalid/x", client=client, sleep=lambda _: None)
    assert exc.value.marker == "captcha"


def test_html_to_text_handles_lists_and_entities() -> None:
    raw = (
        "&lt;ul&gt;&lt;li&gt;Python &amp;amp; SQL&lt;/li&gt;&lt;li&gt;Docker&lt;/li&gt;&lt;/ul&gt;"
    )
    text = html_to_text(raw)
    assert "- Python & SQL" in text
    assert "- Docker" in text
    assert "<" not in text
