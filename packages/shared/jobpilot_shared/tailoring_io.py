"""The tailoring engine's request and response contract (PRD §4.4).

`TailoringOutput` is the shape the LLM is constrained to via `output_config.format`,
and the exact object the whitelist gate validates before anything is rendered.
"""

from pydantic import BaseModel, ConfigDict, Field


class TailoredBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employment_index: int = Field(
        description="Index into canonical_facts.employment this bullet belongs to."
    )
    original: str
    rewritten: str
    skills_referenced: list[str] = Field(
        default_factory=list,
        description="Skills this rewrite leans on. Must be a subset of canonical skills.",
    )


class TailoringOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Tailored 1-2 line headline.")
    tailored_bullets: list[TailoredBullet] = Field(default_factory=list)
    skills_ordered_for_this_jd: list[str] = Field(default_factory=list)

    def all_referenced_skills(self) -> list[str]:
        seen: list[str] = []
        for bullet in self.tailored_bullets:
            seen.extend(bullet.skills_referenced)
        seen.extend(self.skills_ordered_for_this_jd)
        return seen

    def all_prose(self) -> list[str]:
        """Every free-text span the model wrote. What the token scan reads."""
        return [self.summary, *(b.rewritten for b in self.tailored_bullets)]
