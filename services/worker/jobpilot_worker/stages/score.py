"""LLM scoring — the second, expensive filter.

Structure comes from `output_config.format` (via `messages.parse`), not from
parsing free text. There is no `temperature`: sampling parameters are rejected on
Claude Sonnet 5.
"""

import logging

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Event, Job, Score
from jobpilot_shared.location import is_preferred
from jobpilot_shared.scoring_io import ScoreVerdict
from jobpilot_shared.seniority import is_too_senior
from jobpilot_shared.settings import get_settings
from sqlalchemy.orm import Session

from ..clients.llm import LLMClient
from ..prompts import build_scoring_prompt, build_scoring_system

log = logging.getLogger(__name__)


def score_job(facts: CanonicalFacts, job: Job, client: LLMClient) -> ScoreVerdict:
    settings = get_settings()
    return client.parse(
        model=settings.scoring_model,
        max_tokens=settings.scoring_max_tokens,
        system=build_scoring_system(settings.max_years_required),
        prompt=build_scoring_prompt(
            facts, job.title, job.company.name if job.company else "", job.description
        ),
        output_format=ScoreVerdict,
    )


def score_candidates(
    session: Session,
    facts: CanonicalFacts,
    candidates: list,
    client: LLMClient,
) -> list[Score]:
    """Score each candidate, persisting the verdict. Failures skip, never abort.

    The except clause is deliberately broad. It used to name `LLMRefusal` and
    `LLMParseError`, which meant a provider timeout — an `OpenAICompatError`,
    raised by the transport rather than by the parser — took the whole run down
    and rolled back the discovery and embedding work that preceded it. Every way
    a single job can fail to score is a reason to skip that job, never a reason
    to lose the run.
    """
    scored: list[Score] = []
    for candidate in candidates:
        job = candidate.job
        try:
            verdict = score_job(facts, job, client)
        except Exception as exc:
            log.warning("Scoring failed for job %s: %s", job.id, exc)
            session.add(Event(job_id=job.id, type="score.failed", payload={"error": str(exc)}))
            continue

        row = Score(
            job_id=job.id,
            # Band-derived, not the model's raw integer — see scoring_io.
            match_score=verdict.effective_score,
            similarity=getattr(candidate, "similarity", None),
            verdict=verdict.model_dump(),
        )
        session.add(row)
        scored.append(row)
    session.flush()
    return scored


def select_for_tailoring(scores: list[Score], jobs: dict[int, Job] | None = None) -> list[Score]:
    """Choose what the daily run tailors. Bounded by the volume dial, by design
    and not by accident (CLAUDE.md non-negotiable #4).

    **A skills gap is never a reason to drop a job.** Tailoring exists precisely
    to re-emphasise what the candidate does have against what a JD asks for, and
    the gaps themselves are the skills-to-learn report. So the only hard filters
    left are the two the user named:

    1. seniority — 8+ years, or staff/principal/director-and-above scope
    2. location — India and remote roles get the budget; overseas is kept and
       shown in its own tab, promotable by hand from there

    Everything that survives is *ranked*, not filtered: a low band sinks to the
    bottom of the list rather than disappearing. Whatever the daily cap leaves
    behind stays visible as `not_selected` in the shortlist tab.

    The decision is **derived in code**, never taken from the model's own
    `should_apply`. Models were observed returning "skip" on an 88 and "tailor"
    on a 15; letting that gate the queue means one model quirk produces a
    silently empty morning review.
    """
    settings = get_settings()
    jobs = jobs or {}

    eligible: list[Score] = []
    for row in scores:
        verdict = row.verdict or {}
        job = jobs.get(row.job_id)

        # Seniority: the model's own read, backed by a string check on the title
        # and JD so an over-levelled role is caught even when the model is lax.
        if verdict.get("seniority_fit") == "mismatch":
            continue
        if job is not None and is_too_senior(
            job.title, job.description or "", max_years=settings.max_years_required
        ):
            log.info(
                "Job %s dropped: title/JD reads as %s+ years",
                row.job_id,
                settings.max_years_required,
            )
            continue

        # Location: overseas roles are kept in the database and surfaced in their
        # own tab; they just do not spend the daily tailoring budget.
        kind = job.location_kind if job is not None else "unknown"
        if not settings.tailor_overseas and not is_preferred(kind):
            continue

        if verdict.get("should_apply") is False:
            log.info(
                "Job %s: model said should_apply=false but scored %s — selecting on the score.",
                row.job_id,
                row.match_score,
            )
        eligible.append(row)

    # India first, then open remote, then by score. A local role the candidate is
    # a merely-decent fit for beats a remote one they are perfect for.
    def rank(row: Score) -> tuple[int, int]:
        kind = jobs[row.job_id].location_kind if row.job_id in jobs else "unknown"
        return (0 if kind == "india" else 1, -row.match_score)

    eligible.sort(key=rank)
    return eligible[: settings.max_tailored_per_day]
