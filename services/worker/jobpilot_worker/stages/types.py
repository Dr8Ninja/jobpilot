"""Transport objects passed between stages.

Deliberately plain dataclasses rather than ORM rows: discovery and resolution can
be tested without a database, and `ingest` is the single place that touches one.
"""

import hashlib
import html
import re
from dataclasses import dataclass, field

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_PATTERN = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """Greenhouse returns escaped HTML in `content`; scoring wants readable text."""
    text = html.unescape(raw or "")
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = _TAG_PATTERN.sub("", text)
    text = html.unescape(text)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    text = _BLANK_LINES_PATTERN.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def content_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class RawJob:
    """A posting read straight from an ATS board API. Always full-quality."""

    board_token: str
    company_name: str
    ats_job_id: str
    title: str
    location: str | None
    description: str
    apply_url: str
    salary: str | None = None
    ats_provider: str = "greenhouse"

    @property
    def hash(self) -> str:
        return content_hash(self.title, self.location or "", self.description)


@dataclass(frozen=True)
class RawListing:
    """An aggregator search hit. Description is a snippet; the URL is a redirect."""

    external_id: str
    company_name: str
    title: str
    location: str | None
    snippet: str
    redirect_url: str
    salary: str | None = None


@dataclass(frozen=True)
class ResolvedListing:
    """A `RawListing` after following its redirect.

    `board_token` and `ats_job_id` are set only when the destination URL parsed
    into a real Greenhouse job — which is the *only* evidence strong enough to
    drop this row as a duplicate.
    """

    listing: RawListing
    resolved_url: str | None = None
    board_token: str | None = None
    ats_job_id: str | None = None
    #: Populated when the board API could supply the full JD for the resolved job.
    upgraded_description: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_certain_greenhouse_job(self) -> bool:
        return bool(self.board_token and self.ats_job_id)

    @property
    def description(self) -> str:
        return self.upgraded_description or self.listing.snippet

    @property
    def description_quality(self) -> str:
        return "full" if self.upgraded_description else "thin"

    @property
    def hash(self) -> str:
        return content_hash(self.listing.title, self.listing.location or "", self.description)
