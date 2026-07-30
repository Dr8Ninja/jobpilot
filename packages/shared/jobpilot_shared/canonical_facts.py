"""The immutable facts object — the single source of truth for the whole system.

Per CLAUDE.md non-negotiable #2, the tailoring engine may rephrase, reorder, and
re-emphasise what is in here, but `skills`, `experience_years`, employers, titles,
and dates are a locked whitelist. Every model is frozen so nothing downstream can
mutate a fact after the user has confirmed it.

The shape mirrors the user's actual resume — categorised skills, per-role
location, dated projects — because the renderer reproduces that layout rather
than inventing its own.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid")

_MONTHS = {
    "01": "Jan.",
    "02": "Feb.",
    "03": "Mar.",
    "04": "Apr.",
    "05": "May",
    "06": "Jun.",
    "07": "Jul.",
    "08": "Aug.",
    "09": "Sep.",
    "10": "Oct.",
    "11": "Nov.",
    "12": "Dec.",
}


def format_month(value: str | None) -> str:
    """`2024-07` -> `Jul. 2024`, `present` -> `Present`. Anything else passes through."""
    if not value:
        return ""
    if value.strip().lower() in {"present", "current", "now"}:
        return "Present"
    parts = value.strip().split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1] in _MONTHS:
        return f"{_MONTHS[parts[1]]} {parts[0]}"
    return value


def format_range(start: str | None, end: str | None) -> str:
    left, right = format_month(start), format_month(end)
    if left and right:
        return f"{left} – {right}"
    return left or right


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

    def linkedin_handle(self) -> str | None:
        """`https://www.linkedin.com/in/vishal-ratwaya/` -> `vishal-ratwaya`."""
        if not self.linkedin:
            return None
        return self.linkedin.rstrip("/").rsplit("/", 1)[-1] or None


class Employment(BaseModel):
    model_config = _FROZEN

    company: str
    title: str
    start: str = Field(description="YYYY-MM")
    end: str = Field(description="YYYY-MM or 'present'")
    location: str | None = None
    bullets: tuple[str, ...] = ()

    def date_range(self) -> str:
        return format_range(self.start, self.end)


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

    def date_range(self) -> str:
        return format_range(self.start, self.end)


class Education(BaseModel):
    model_config = _FROZEN

    degree: str
    institution: str
    year: str
    start: str | None = None
    end: str | None = None
    location: str | None = None

    def date_range(self) -> str:
        return format_range(self.start, self.end) or self.year


class SkillCategory(BaseModel):
    """One labelled row of the resume's SKILLS block, e.g. `Languages : Java, C++`.

    Display only. `CanonicalFacts.skills` remains the authoritative whitelist the
    gate checks against; every entry here must appear there.
    """

    model_config = _FROZEN

    label: str
    items: tuple[str, ...]


class CanonicalFacts(BaseModel):
    model_config = _FROZEN

    identity: Identity
    links: Links = Links()
    experience_years: float
    #: The authoritative whitelist. Flat, unordered, gate-enforced.
    skills: tuple[str, ...]
    #: How the skills are grouped on the printed resume. Optional — without it the
    #: renderer falls back to a single flat line.
    skill_categories: tuple[SkillCategory, ...] = ()
    employment: tuple[Employment, ...] = ()
    projects: tuple[Project, ...] = ()
    education: tuple[Education, ...] = ()

    @model_validator(mode="after")
    def _categories_subset_of_skills(self) -> "CanonicalFacts":
        """A category must not smuggle in a skill the whitelist has never seen.

        Without this, the SKILLS block on the PDF could print something the gate
        would have rejected anywhere else — the categories are rendered directly.
        """
        if not self.skill_categories:
            return self
        known = {s.casefold().strip() for s in self.skills}
        unknown = [
            item
            for category in self.skill_categories
            for item in category.items
            if item.casefold().strip() not in known
        ]
        if unknown:
            raise ValueError(
                "skill_categories contains entries missing from skills: "
                + ", ".join(sorted(set(unknown)))
            )
        return self

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
