"""Upsert discovered postings, applying the certainty dedupe rule.

Precedence when two sources describe the same job: **Greenhouse wins**, because
its row carries a full structured JD while the aggregator's carries a snippet.
The loser is marked `superseded_by` rather than deleted, so how often the
aggregator was redundant stays measurable.

The certainty rule is what decides whether two rows *are* the same job: a shared
`(company_id, ats_job_id)`, and nothing weaker.
"""

import logging
import re
from dataclasses import dataclass, field

from jobpilot_shared.db.models import Company, Event, Job
from sqlalchemy import select
from sqlalchemy.orm import Session

from .types import RawJob, RawListing, ResolvedListing, content_hash

log = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_company_name(name: str) -> str:
    """Company identity key. Drops punctuation and common legal suffixes."""
    key = _NON_ALNUM.sub(" ", name.casefold()).strip()
    for suffix in (
        " incorporated",
        " inc",
        " llc",
        " ltd",
        " limited",
        " plc",
        " gmbh",
        " pvt",
        " private",
        " corporation",
        " corp",
        " co",
    ):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    return _NON_ALNUM.sub("", key)


@dataclass
class IngestReport:
    inserted: int = 0
    updated: int = 0
    deduped: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "IngestReport") -> "IngestReport":
        return IngestReport(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            deduped=self.deduped + other.deduped,
            skipped=self.skipped + other.skipped,
            notes=self.notes + other.notes,
        )


def upsert_company(
    session: Session,
    name: str,
    *,
    ats_provider: str | None = None,
    board_token: str | None = None,
    discovered_via: str = "seed",
) -> Company:
    key = normalize_company_name(name)
    company = session.scalar(select(Company).where(Company.normalized_name == key))
    if company is None:
        company = Company(
            name=name,
            normalized_name=key,
            ats_provider=ats_provider,
            board_token=board_token,
            discovered_via=discovered_via,
        )
        session.add(company)
        session.flush()
        return company

    # Learning a board token for a company we only knew from the aggregator is
    # the registry growing — the one thing the aggregator was pulled forward for.
    if board_token and not company.board_token:
        company.board_token = board_token
        company.ats_provider = ats_provider or company.ats_provider
        session.flush()
    return company


def _existing_by_identity(session: Session, company_id: int, ats_job_id: str) -> Job | None:
    return session.scalar(
        select(Job).where(Job.company_id == company_id, Job.ats_job_id == ats_job_id)
    )


def _existing_by_source(session: Session, source: str, external_id: str) -> Job | None:
    return session.scalar(select(Job).where(Job.source == source, Job.external_id == external_id))


def ingest_ats_job(session: Session, raw: RawJob) -> IngestReport:
    report = IngestReport()
    company = upsert_company(
        session,
        raw.company_name,
        ats_provider=raw.ats_provider,
        board_token=raw.board_token,
        discovered_via="seed",
    )

    existing = _existing_by_source(session, raw.ats_provider, raw.ats_job_id)
    if existing is not None:
        if existing.content_hash != raw.hash:
            existing.title = raw.title
            existing.location = raw.location
            existing.description = raw.description
            existing.apply_url = raw.apply_url
            existing.content_hash = raw.hash
            report.updated += 1
        else:
            report.skipped += 1
        return report

    # An aggregator row may already occupy this identity. Greenhouse wins: release
    # the loser's claim on the index, insert, then record what superseded it.
    loser = _existing_by_identity(session, company.id, raw.ats_job_id)
    if loser is not None:
        loser.ats_job_id = None
        session.flush()

    job = Job(
        company_id=company.id,
        source=raw.ats_provider,
        external_id=raw.ats_job_id,
        ats_job_id=raw.ats_job_id,
        title=raw.title,
        location=raw.location,
        description=raw.description,
        apply_url=raw.apply_url,
        description_quality="full",
        salary=raw.salary,
        content_hash=raw.hash,
    )
    session.add(job)
    session.flush()
    report.inserted += 1

    if loser is not None:
        loser.superseded_by = job.id
        session.add(
            Event(
                job_id=job.id,
                type="job.superseded",
                payload={
                    "superseded_job_id": loser.id,
                    "reason": (
                        f"{raw.ats_provider} row replaces aggregator row with same ats_job_id"
                    ),
                },
            )
        )
        session.flush()
        report.deduped += 1
        report.notes.append(f"aggregator job {loser.id} superseded by greenhouse {job.id}")

    return report


def ingest_resolved_listing(session: Session, resolved: ResolvedListing) -> IngestReport:
    report = IngestReport()
    listing = resolved.listing
    company = upsert_company(
        session,
        listing.company_name,
        ats_provider="greenhouse" if resolved.board_token else None,
        board_token=resolved.board_token,
        discovered_via="aggregator",
    )

    existing = _existing_by_source(session, "adzuna", listing.external_id)
    if existing is not None:
        report.skipped += 1
        return report

    # THE CERTAINTY RULE. Drop this row only when a Greenhouse job id proves it is
    # the same posting. Anything weaker keeps both rows.
    if resolved.is_certain_greenhouse_job:
        assert resolved.ats_job_id is not None
        duplicate_of = _existing_by_identity(session, company.id, resolved.ats_job_id)
        if duplicate_of is not None:
            session.add(
                Event(
                    job_id=duplicate_of.id,
                    type="job.duplicate_dropped",
                    payload={
                        "aggregator_external_id": listing.external_id,
                        "ats_job_id": resolved.ats_job_id,
                    },
                )
            )
            session.flush()
            report.deduped += 1
            report.notes.append(
                f"aggregator {listing.external_id} is job {duplicate_of.id}; dropped"
            )
            return report

    job = Job(
        company_id=company.id,
        source="adzuna",
        external_id=listing.external_id,
        ats_job_id=resolved.ats_job_id,
        title=listing.title,
        location=listing.location,
        description=resolved.description,
        apply_url=listing.redirect_url,
        resolved_url=resolved.resolved_url,
        description_quality=resolved.description_quality,
        salary=listing.salary,
        content_hash=resolved.hash,
    )
    session.add(job)
    session.flush()
    report.inserted += 1
    return report


def ingest_remote_listing(session: Session, source: str, listing: "RawListing") -> IngestReport:
    """Insert a row from a keyless remote board.

    These carry full descriptions, so unlike an aggregator snippet they are
    `description_quality='full'` and compete with ATS rows on equal footing.
    They have no ATS job id, so they never participate in certainty dedupe.
    """
    report = IngestReport()
    company = upsert_company(session, listing.company_name, discovered_via="aggregator")

    if _existing_by_source(session, source, listing.external_id) is not None:
        report.skipped += 1
        return report

    session.add(
        Job(
            company_id=company.id,
            source=source,
            external_id=listing.external_id,
            ats_job_id=None,
            title=listing.title,
            location=listing.location,
            description=listing.snippet,
            apply_url=listing.redirect_url,
            description_quality="full",
            salary=listing.salary,
            content_hash=content_hash(listing.title, listing.location or "", listing.snippet),
        )
    )
    session.flush()
    report.inserted += 1
    return report
