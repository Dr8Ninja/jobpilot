"""Prompt construction for scoring and tailoring.

The canonical facts are embedded as JSON so the model has the exact whitelist in
front of it, and so the gate's rejections can point at concrete field names when
a retry is needed.
"""

import json

from jobpilot_shared.canonical_facts import CanonicalFacts

TAILORING_SYSTEM = """\
You rewrite one candidate's existing resume bullets to fit a specific job description.

The candidate's resume layout is fixed and is not yours to change. Return EXACTLY
ONE rewritten bullet for EVERY bullet in canonical_facts.employment, in the same
order, carrying that bullet's `employment_index` and its text in `original`. A
role with five bullets gets five rewrites. Never merge two bullets, never drop
one because it seems less relevant, and never add a sixth — the document must
have the same shape as the one the candidate wrote.

Keep each rewrite close to the length of the bullet it replaces (within roughly
10%). The resume is one page and stays one page; a rewrite twice as long as the
original breaks the layout, and a one-line rewrite of a three-line bullet throws
away real detail the candidate earned.

You may:
- rephrase each bullet, keeping its subject and its accomplishment
- lead with the part of the bullet this job cares about most
- surface skills the candidate already has that the job asks for
- mirror the job description's vocabulary where it honestly describes existing work

You must never:
- introduce a skill, tool, language, or technology that is not in canonical_facts.skills
- change or imply a different experience_years, job title, employer, or date
- name any company the candidate has not worked for
- invent a project, a metric, or a responsibility
- drop, merge, shorten past recognition, or invent a bullet

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
- Reserve 'mismatch' for exactly two cases: more than {max_years} years of
  experience required, or a staff / principal / director / VP-level scope.
  Nothing else is a seniority mismatch.

Judge skills and domain fit on their own merits; do not let a 3-8 year
requirement drag an otherwise strong technical match down to 'weak'.

A missing skill is NOT a reason to fail the candidate. Record it in
`keyword_gaps` — that list is what the tailoring stage emphasises against, and
what the candidate uses to decide what to learn — then band the role on whether
they could do the job, not on whether they tick every listed keyword. Roles the
candidate could grow into within a few weeks are 'moderate' at worst.
"""


def build_scoring_system(max_years: int) -> str:
    return SCORING_SYSTEM_TEMPLATE.format(max_years=max_years)


#: Back-compat for callers that want the default window.
SCORING_SYSTEM = SCORING_SYSTEM_TEMPLATE.format(max_years=8)


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
List at most 10, each a short skill name rather than a phrase. Keep `rationale`
to a few sentences; a long answer risks being cut off before it is complete.
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
    # Spelling out the expected count, and the per-role split, measurably helps.
    # The renderer guarantees the shape regardless, but a short reply means real
    # bullets fall back to their original wording instead of being tailored.
    per_role = ", ".join(
        f"index {i} needs {len(role.bullets)}" for i, role in enumerate(facts.employment)
    )
    bullet_budget = sum(len(role.bullets) for role in facts.employment)
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

Rewrite the candidate's bullets for this job. Produce one tailored bullet for
every bullet listed under canonical_facts.employment — {bullet_budget} in total
({per_role}), in the order they appear — each carrying the `employment_index` of
the role it belongs to and the untouched original text in `original`. Same count,
same order, comparable length: only the wording changes.

`skills_ordered_for_this_jd` should rank the candidate's existing skills by
relevance to this job.

The keyword gaps are context, not a shopping list: do not claim any of them the
candidate does not already have.
"""
    if retry_constraints:
        prompt += f"\n<fact_check_failures>\n{retry_constraints}\n</fact_check_failures>\n"
    return prompt
