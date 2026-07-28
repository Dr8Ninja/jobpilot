"""Prompt construction for scoring and tailoring.

The canonical facts are embedded as JSON so the model has the exact whitelist in
front of it, and so the gate's rejections can point at concrete field names when
a retry is needed.
"""

import json

from jobpilot_shared.canonical_facts import CanonicalFacts

TAILORING_SYSTEM = """\
You rewrite one candidate's existing resume bullets to fit a specific job description.

You may:
- reorder and rephrase bullets that already exist
- surface skills the candidate already has that the job asks for
- mirror the job description's vocabulary where it honestly describes existing work

You must never:
- introduce a skill, tool, language, or technology that is not in canonical_facts.skills
- change or imply a different experience_years, job title, employer, or date
- name any company the candidate has not worked for
- invent a project, a metric, or a responsibility

Every entry in skills_referenced must appear verbatim in canonical_facts.skills.
An automated fact-check runs on your output and rejects violations, so inventing
anything wastes the attempt. When in doubt, stay closer to the original bullet.
"""

SCORING_SYSTEM_TEMPLATE = """\
You score how well one candidate matches one job description.

Be calibrated and honest: a high score should mean the candidate would plausibly
clear a recruiter screen.

On seniority, the candidate is deliberately willing to stretch:
- A role asking for up to {max_years} years of experience is a REAL opportunity.
  Mark seniority_fit 'good' when the requirement is at or below their experience,
  and 'stretch' when it is above but still within {max_years} years. A stretch is
  worth applying to — do not mark it 'mismatch' and do not tank the band for it.
- Reserve 'mismatch' for roles that are genuinely out of range: more than
  {max_years} years required, or a staff/principal/director-level scope, or a
  different discipline entirely.

Judge skills and domain fit on their own merits; do not let a 3-5 year
requirement drag an otherwise strong technical match down to 'weak'.
"""


def build_scoring_system(max_years: int) -> str:
    return SCORING_SYSTEM_TEMPLATE.format(max_years=max_years)


#: Back-compat for callers that want the default window.
SCORING_SYSTEM = SCORING_SYSTEM_TEMPLATE.format(max_years=5)


def _facts_json(facts: CanonicalFacts) -> str:
    return json.dumps(facts.model_dump(), indent=2, ensure_ascii=False)


def build_scoring_prompt(facts: CanonicalFacts, title: str, company: str, jd: str) -> str:
    return f"""\
<canonical_facts>
{_facts_json(facts)}
</canonical_facts>

<job>
Company: {company}
Title: {title}

{jd.strip()[:12000]}
</job>

Score this match. `keyword_gaps` should list terms the job asks for that the
candidate's facts do not cover — those become emphasis hints for tailoring.
"""


def build_tailoring_prompt(
    facts: CanonicalFacts,
    title: str,
    company: str,
    jd: str,
    keyword_gaps: list[str],
    retry_constraints: str = "",
) -> str:
    gaps = ", ".join(keyword_gaps) if keyword_gaps else "(none identified)"
    prompt = f"""\
<canonical_facts>
{_facts_json(facts)}
</canonical_facts>

<job>
Company: {company}
Title: {title}

{jd.strip()[:12000]}
</job>

<keyword_gaps>
{gaps}
</keyword_gaps>

Rewrite the candidate's bullets for this job. Produce one tailored bullet per
original bullet you choose to include, each carrying the `employment_index` of the
role it belongs to. `skills_ordered_for_this_jd` should rank the candidate's
existing skills by relevance to this job.

The keyword gaps are context, not a shopping list: do not claim any of them the
candidate does not already have.
"""
    if retry_constraints:
        prompt += f"\n<fact_check_failures>\n{retry_constraints}\n</fact_check_failures>\n"
    return prompt
