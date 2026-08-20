"""Response shapes for the review dashboard."""

from datetime import datetime
from typing import Literal

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
    #: india / remote / overseas / unknown — drives the Overseas tab.
    location_kind: str
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
    location_kind: str
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


class SkillGapExample(BaseModel):
    company: str
    title: str
    job_id: int


class SkillGapRow(BaseModel):
    """One entry in the skills-to-learn report."""

    skill: str
    job_count: int
    companies: list[str]
    examples: list[SkillGapExample]


class RunRequest(BaseModel):
    """What to run. `application_id` is required for, and only for, `tailor`."""

    kind: Literal["pipeline", "discovery", "tailor"]
    application_id: int | None = None


class RunStatus(BaseModel):
    """A background run, as the dashboard polls it."""

    id: int
    kind: str
    status: str
    params: dict | None
    summary: dict | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class TailorAccepted(BaseModel):
    """202 from the tailor endpoint: the work is queued, not done."""

    application_id: int
    run_id: int
    status: str
