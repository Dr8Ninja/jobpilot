"""Which skills to learn, and for which companies.

The scoring stage already records, per job, the terms a JD asked for that the
candidate's facts do not cover. Individually those are just tailoring hints. In
aggregate they are a study list: the skill that blocks eleven roles is worth a
weekend, the one that blocks a single job is not.

This module is the aggregation, kept pure so it can be tested without a database
— the API supplies the rows, this decides what the report says.

Nothing here ever touches the resume. A gap is a thing to go and *learn*, never
a thing to claim; the whitelist gate still rejects any skill not in
`canonical_facts.skills`.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .whitelist import normalize_skill

#: Terms that appear constantly in JDs but are not skills anyone studies.
#: Normalised on the way in — `normalize_skill` drops spaces, so the literal
#: strings here would never match a gap without it.
_NOT_A_SKILL = {
    normalize_skill(term)
    for term in (
        "experience",
        "years",
        "degree",
        "bachelor",
        "bachelors",
        "master",
        "masters",
        "communication",
        "communication skills",
        "teamwork",
        "team player",
        "problem solving",
        "problem-solving",
        "leadership",
        "ownership",
        "computer science",
        "cs degree",
        "english",
        "attention to detail",
        "collaboration",
        "self starter",
        "self-starter",
        "fast paced",
        "startup",
        "mentoring",
        "mentorship",
        "stakeholder management",
        "agile",
        "scrum",
    )
}

#: "5 years experience", "3+ yrs" — a requirement restated as a gap. Seniority is
#: handled elsewhere and is not something you can go and learn.
_A_REQUIREMENT_NOT_A_SKILL = re.compile(
    r"\d\s*\+?\s*(?:yrs?|years?)|\b(?:years?|yrs?)\s+of\b|\bdegree\b", re.IGNORECASE
)

#: One technology, several names. Without this the report splits a nine-job skill
#: into "Go 7" and "Golang 3" and buries it — observed on the live data.
_ALIASES = {
    normalize_skill(alias): canonical
    for alias, canonical in {
        "golang": "Go",
        "go lang": "Go",
        "js": "JavaScript",
        "ts": "TypeScript",
        "node": "Node.js",
        "nodejs": "Node.js",
        "postgres": "PostgreSQL",
        "psql": "PostgreSQL",
        "k8s": "Kubernetes",
        "gcp": "Google Cloud",
        "google cloud platform": "Google Cloud",
        "aws cloud": "AWS",
        "amazon web services": "AWS",
        "spark": "Apache Spark",
        "kafka": "Apache Kafka",
        "airflow": "Apache Airflow",
        "ci cd": "CI/CD",
        "cicd": "CI/CD",
        "llms": "LLMs",
        "large language models": "LLMs",
        "genai": "generative AI",
        "gen ai": "generative AI",
        "ml": "machine learning",
        "iac": "infrastructure as code",
        "tf": "Terraform",
    }.items()
}


@dataclass
class SkillGap:
    """One skill the candidate does not have, and who is asking for it."""

    skill: str
    #: How many distinct jobs asked for it. The whole point of the report.
    job_count: int = 0
    companies: list[str] = field(default_factory=list)
    #: A few concrete postings, so the user can see the context before studying.
    examples: list[dict] = field(default_factory=list)


def aggregate_gaps(
    rows: list[tuple[str, str, str, int]],
    *,
    known_skills: tuple[str, ...] = (),
    min_jobs: int = 1,
    limit: int = 60,
) -> list[SkillGap]:
    """Roll per-job `keyword_gaps` up into a ranked study list.

    `rows` are `(skill, company, job_title, job_id)`. Skills the candidate
    already has are dropped: models routinely list something as "missing" that
    is sitting in the facts under a different casing, and a study list that
    tells you to learn Python when you know Python is noise.
    """
    known = {normalize_skill(s) for s in known_skills}
    known.discard("")

    buckets: dict[str, SkillGap] = {}
    display: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen_pairs: set[tuple[str, int]] = set()

    for raw_skill, company, title, job_id in rows:
        skill = (raw_skill or "").strip().strip(".,;:")
        key = normalize_skill(skill)
        if key in _ALIASES:
            skill = _ALIASES[key]
            key = normalize_skill(skill)
        if not key or key in known or key in _NOT_A_SKILL:
            continue
        if _A_REQUIREMENT_NOT_A_SKILL.search(skill):
            continue
        # A phrase this long is a sentence fragment, not a skill to go and learn.
        if len(skill) > 40 or len(skill.split()) > 4:
            continue
        if (key, job_id) in seen_pairs:
            continue
        seen_pairs.add((key, job_id))

        display[key][skill] += 1
        gap = buckets.setdefault(key, SkillGap(skill=skill))
        gap.job_count += 1
        if company and company not in gap.companies:
            gap.companies.append(company)
        if len(gap.examples) < 8:
            gap.examples.append({"company": company, "title": title, "job_id": job_id})

    for key, gap in buckets.items():
        # Use whichever spelling the JDs used most — "Kubernetes" over "kubernetes".
        gap.skill = max(display[key].items(), key=lambda kv: (kv[1], kv[0]))[0]

    ranked = [g for g in buckets.values() if g.job_count >= min_jobs]
    ranked.sort(key=lambda g: (-g.job_count, g.skill.casefold()))
    return ranked[:limit]
