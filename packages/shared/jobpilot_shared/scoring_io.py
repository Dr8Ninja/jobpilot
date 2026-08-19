"""Structured verdict returned by the scoring stage (PRD §4.3).

Two findings from measuring real models drove this shape.

**Field order is load-bearing.** Structured output is generated left to right, so
a model asked for the score first must commit to a number before it has reasoned.
Reasoning fields come first so the verdict is a conclusion, not a guess.

**Categorical judgments are reliable; free integers are not.** Across repeated
runs on an identical prompt, `seniority_fit` was correct every time while
`match_score` came back as `[92, 88, 0, 90]` from one model and `[9, 8, 88, 8]`
from another. A single stray 0 silently starves the tailoring stage, so the
number the pipeline actually ranks and thresholds on is derived from `fit_band`
in code. The model's own integer is kept for observability and tie-breaking only.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SeniorityFit = Literal["good", "stretch", "mismatch"]
FitBand = Literal["excellent", "strong", "moderate", "weak", "none"]

#: Band -> the score the pipeline thresholds on. Spaced so `strong` clears a
#: default threshold of 70 and `moderate` does not.
BAND_SCORES: dict[str, int] = {
    "excellent": 92,
    "strong": 78,
    "moderate": 62,
    "weak": 38,
    "none": 12,
}


class ScoreVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Both lists are capped in the *schema*, not merely requested in the prompt.
    #: Strict structured decoding enforces `maxItems`, and that is what actually
    #: stops the runaway: on some inputs the model kept emitting requirement
    #: strings until it hit the token ceiling, then padded with whitespace and
    #: returned JSON it never closed. A bigger ceiling just makes that failure
    #: more expensive — 6,713 characters of padding and 58 seconds in one
    #: capture. Ten gaps is also all the skills report can usefully show.
    must_have_coverage: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Each stated requirement marked met or missing, e.g. 'Java: met'.",
    )
    keyword_gaps: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Terms the job asks for that the candidate's facts do not cover.",
    )
    seniority_fit: SeniorityFit = Field(
        description=(
            "Is the candidate's years of experience right for this role? "
            "'good' = within the stated range. 'stretch' = somewhat under. "
            "'mismatch' = the role wants far more (or far less) experience."
        )
    )
    rationale: str = Field(
        max_length=900,
        description="One paragraph justifying the band you are about to choose.",
    )
    fit_band: FitBand = Field(
        description=(
            "Overall fit. "
            "'excellent' = covers essentially all requirements at the right level. "
            "'strong' = covers most requirements, minor gaps. "
            "'moderate' = covers about half, real gaps. "
            "'weak' = few requirements met. "
            "'none' = wrong discipline or badly wrong seniority."
        )
    )
    match_score: int = Field(
        ge=0,
        le=100,
        description="0-100, consistent with the band you chose. Used only for ranking.",
    )
    should_apply: bool = Field(
        description="true if this candidate should apply to this job, false otherwise."
    )

    @property
    def effective_score(self) -> int:
        """The number the pipeline ranks and thresholds on.

        Derived from the band, nudged by the model's own integer only when the
        two already agree — so a stray 0 cannot drop an otherwise strong match.
        """
        base = BAND_SCORES[self.fit_band]
        if abs(self.match_score - base) <= 15:
            return round((base + self.match_score) / 2)
        return base

    def is_coherent(self) -> bool:
        """Does the model's own integer agree with its own band? Advisory only."""
        return abs(self.match_score - BAND_SCORES[self.fit_band]) <= 15
