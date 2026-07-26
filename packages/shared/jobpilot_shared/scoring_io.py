"""Structured verdict returned by the scoring stage (PRD §4.3)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SeniorityFit = Literal["good", "stretch", "mismatch"]
Recommendation = Literal["tailor", "skip"]


class ScoreVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(ge=0, le=100)
    must_have_coverage: list[str] = Field(
        default_factory=list,
        description="Each requirement marked met or missing, e.g. 'React: met'.",
    )
    keyword_gaps: list[str] = Field(
        default_factory=list,
        description="JD terms absent from the resume. Fed to tailoring as emphasis hints.",
    )
    seniority_fit: SeniorityFit
    recommendation: Recommendation
    rationale: str
