"""Relational schema (design doc §6).

Two indexes carry the cross-source dedupe rule:

* ``UNIQUE(source, external_id)`` — the always-safe idempotency key. Re-running
  discovery never duplicates a row.
* a **partial** unique index on ``(company_id, ats_job_id)`` — the certainty rule.
  A Greenhouse row and a resolved aggregator row that share a real Greenhouse job
  id physically cannot coexist. Unresolved aggregator rows carry
  ``ats_job_id = NULL``, and Postgres treats NULLs as distinct, so they are never
  falsely collapsed.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIMENSIONS = 1024  # voyage-3

#: Per-company ATS boards. All documented, public, unauthenticated JSON APIs.
ATS_PROVIDERS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters")
#: Where a job row came from. ATS providers plus keyless remote boards and the
#: paid aggregator. No entry here is ever populated by scraping.
JOB_SOURCES = (
    "greenhouse",
    "lever",
    "ashby",
    "workable",
    "smartrecruiters",
    "adzuna",
    "remotive",
    "arbeitnow",
    "remoteok",
)
DESCRIPTION_QUALITIES = ("full", "thin")
LOCATION_KINDS = ("india", "remote", "overseas", "unknown")
DISCOVERY_ORIGINS = ("seed", "aggregator")
APPLICATION_STATUSES = (
    "queued",
    "approved",
    "applied",
    "rejected",
    "needs_human",
    #: Scored and kept, but below the tailoring threshold or outside the daily
    #: cap. Visible in its own tab so nothing is silently dropped — the user can
    #: promote one and it gets tailored on demand.
    "not_selected",
    "failed",
)


class Base(DeclarativeBase):
    pass


def _enum_check(column: str, allowed: tuple[str, ...], name: str) -> CheckConstraint:
    values = ", ".join(f"'{v}'" for v in allowed)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    """The confirmed canonical_facts object. One row; this is a single-user tool."""

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_facts: Mapped[dict] = mapped_column(JSONB)
    base_resume_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        # board_token is nullable: an aggregator-discovered company may have no
        # resolvable ATS board at all. Postgres treats NULLs as distinct, so the
        # partial index lets many such rows coexist while still preventing two
        # companies from claiming the same real board.
        Index(
            "uq_companies_provider_board",
            "ats_provider",
            "board_token",
            unique=True,
            postgresql_where="board_token IS NOT NULL",
        ),
        CheckConstraint(
            "ats_provider IS NULL OR ats_provider IN "
            "('greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters')",
            name="ck_companies_provider",
        ),
        _enum_check("discovered_via", DISCOVERY_ORIGINS, "ck_companies_discovered_via"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True)
    ats_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    board_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    discovered_via: Mapped[str] = mapped_column(String(32), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external"),
        Index(
            "uq_jobs_company_ats_job",
            "company_id",
            "ats_job_id",
            unique=True,
            postgresql_where="ats_job_id IS NOT NULL",
        ),
        _enum_check("source", JOB_SOURCES, "ck_jobs_source"),
        _enum_check("description_quality", DESCRIPTION_QUALITIES, "ck_jobs_quality"),
        _enum_check("location_kind", LOCATION_KINDS, "ck_jobs_location_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))

    source: Mapped[str] = mapped_column(String(32))
    #: Provider-supplied. 128 was too tight: Arbeitnow builds its slug from every
    #: location a posting names, and one job spanning seven of them overflowed and
    #: aborted a whole discovery run. Bounded rather than Text because it carries a
    #: unique index; `bound_external_id` collapses anything longer.
    external_id: Mapped[str] = mapped_column(String(512))
    #: Greenhouse job id. NULL for aggregator rows whose destination did not resolve.
    ats_job_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    title: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(Text)
    #: Destination after following the aggregator redirect, when we followed one.
    resolved_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    description_quality: Mapped[str] = mapped_column(String(16), default="full")
    #: Sparse by design — most ATS payloads omit it. Never an input to scoring.
    salary: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: india / remote / overseas / unknown. India and remote rank first; overseas
    #: gets its own tab rather than being dropped or mixed in.
    location_kind: Mapped[str] = mapped_column(String(16), default="unknown", index=True)

    #: When the employer published the posting, per the provider. NULL when the
    #: provider exposes no date — such rows are excluded by the freshness filter
    #: rather than assumed fresh.
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    content_hash: Mapped[str] = mapped_column(String(64))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    #: Set when this row was dropped in favour of a richer duplicate. Recorded
    #: rather than deleted so the aggregator's redundancy rate is measurable.
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    company: Mapped[Company] = relationship(back_populates="jobs")


class JobEmbedding(Base):
    __tablename__ = "job_embeddings"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    model: Mapped[str] = mapped_column(String(64))
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    match_score: Mapped[int] = mapped_column(Integer)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[dict] = mapped_column(JSONB)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TailoringRun(Base):
    __tablename__ = "tailoring_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    output: Mapped[dict] = mapped_column(JSONB)
    #: Hard gate. Nothing with False here may be rendered or shown as approvable.
    whitelist_passed: Mapped[bool] = mapped_column(default=False)
    gate_rejections: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    gate_warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        _enum_check("status", APPLICATION_STATUSES, "ck_applications_status"),
        UniqueConstraint("job_id", name="uq_applications_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    tailoring_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("tailoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """Audit trail. Unused by any Phase 0 feature, written to from day one.

    This is the substrate the response-rate baseline is computed from later —
    cheap to write now, impossible to backfill.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
