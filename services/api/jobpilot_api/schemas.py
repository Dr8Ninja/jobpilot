"""Response shapes for the review dashboard."""

from datetime import datetime

from pydantic import BaseModel


class GateNote(BaseModel):
    rule: str
    severity: str
    detail: str
    evidence: str


class QueueCard(BaseModel):
    """One row in the review queue."""

    application_id: int
    job_id: int
    company: str
    title: str
    location: str | None
    #: Sparse by design — most ATS payloads omit salary.
    salary: str | None
    match_score: int | None
    status: str
    source: str
    description_quality: str
    apply_url: str
    has_pdf: bool
    warning_count: int
    #: When the employer published it. None when the provider exposes no date.
    posted_at: datetime | None
    created_at: datetime


class BulletDiff(BaseModel):
    employment_index: int
    company: str
    original: str
    rewritten: str
    skills_referenced: list[str]
    changed: bool


class QueueDetail(BaseModel):
    application_id: int
    job_id: int
    company: str
    title: str
    location: str | None
    salary: str | None
    status: str
    source: str
    description_quality: str
    apply_url: str
    description: str

    match_score: int | None
    rationale: str | None
    must_have_coverage: list[str]
    keyword_gaps: list[str]
    seniority_fit: str | None

    summary: str
    diffs: list[BulletDiff]
    skills_ordered: list[str]

    whitelist_passed: bool
    warnings: list[GateNote]
    rejections: list[GateNote]
    attempts: int
    has_pdf: bool
    posted_at: datetime | None


class StatusCount(BaseModel):
    status: str
    count: int
