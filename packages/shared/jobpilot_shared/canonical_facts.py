"""The immutable facts object — the single source of truth for the whole system.

Per CLAUDE.md non-negotiable #2, the tailoring engine may rephrase, reorder, and
re-emphasise what is in here, but `skills`, `experience_years`, employers, titles,
and dates are a locked whitelist. Every model is frozen so nothing downstream can
mutate a fact after the user has confirmed it.
"""

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Identity(BaseModel):
    model_config = _FROZEN

    name: str
    email: str
    phone: str | None = None
    location: str | None = None


class Links(BaseModel):
    model_config = _FROZEN

    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None


class Employment(BaseModel):
    model_config = _FROZEN

    company: str
    title: str
    start: str = Field(description="YYYY-MM")
    end: str = Field(description="YYYY-MM or 'present'")
    bullets: tuple[str, ...] = ()


class Project(BaseModel):
    """Personal, academic, or side work.

    Carries real weight for early-career candidates, so the tailoring prompt sees
    these and the renderer prints them. Bullets are rendered verbatim from here —
    the model may reference a project in the summary, but cannot rewrite it.
    """

    model_config = _FROZEN

    name: str
    role: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: tuple[str, ...] = ()


class Education(BaseModel):
    model_config = _FROZEN

    degree: str
    institution: str
    year: str


class CanonicalFacts(BaseModel):
    model_config = _FROZEN

    identity: Identity
    links: Links = Links()
    experience_years: float
    skills: tuple[str, ...]
    employment: tuple[Employment, ...] = ()
    projects: tuple[Project, ...] = ()
    education: tuple[Education, ...] = ()

    def employer_names(self) -> tuple[str, ...]:
        return tuple(e.company for e in self.employment)

    def source_prose(self) -> tuple[str, ...]:
        """Every sentence the user actually wrote about themselves.

        The provenance set: a technology named here is one the candidate can
        legitimately claim even if it never made the declared skills list.
        """
        spans: list[str] = []
        for role in self.employment:
            spans.append(role.title)
            spans.extend(role.bullets)
        for project in self.projects:
            spans.append(project.name)
            if project.role:
                spans.append(project.role)
            spans.extend(project.bullets)
        for edu in self.education:
            spans.append(edu.degree)
        return tuple(spans)
