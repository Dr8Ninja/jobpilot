"""LLM scoring — the second, expensive filter.

Structure comes from `output_config.format` (via `messages.parse`), not from
parsing free text. There is no `temperature`: sampling parameters are rejected on
Claude Sonnet 5.
"""

import logging

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Event, Job, Score
from jobpilot_shared.scoring_io import ScoreVerdict
from jobpilot_shared.settings import get_settings
from sqlalchemy.orm import Session

from ..clients.llm import LLMClient, LLMParseError, LLMRefusal
from ..prompts import SCORING_SYSTEM, build_scoring_prompt

log = logging.getLogger(__name__)


def score_job(facts: CanonicalFacts, job: Job, client: LLMClient) -> ScoreVerdict:
    settings = get_settings()
    return client.parse(
        model=settings.scoring_model,
        max_tokens=settings.scoring_max_tokens,
        system=SCORING_SYSTEM,
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
    """Score each candidate, persisting the verdict. Failures skip, never abort."""
    scored: list[Score] = []
    for candidate in candidates:
        job = candidate.job
        try:
            verdict = score_job(facts, job, client)
        except (LLMRefusal, LLMParseError) as exc:
            log.warning("Scoring failed for job %s: %s", job.id, exc)
            session.add(Event(job_id=job.id, type="score.failed", payload={"error": str(exc)}))
            continue

        row = Score(
            job_id=job.id,
            match_score=verdict.match_score,
            similarity=getattr(candidate, "similarity", None),
            verdict=verdict.model_dump(),
        )
        session.add(row)
        scored.append(row)
    session.flush()
    return scored


def select_for_tailoring(scores: list[Score]) -> list[Score]:
    """Apply the threshold and the daily volume dial.

    This is where the queue is bounded well below carpet-bomb levels — by design,
    not by accident (CLAUDE.md non-negotiable #4).
    """
    settings = get_settings()
    eligible = [
        s
        for s in scores
        if s.match_score >= settings.match_score_threshold
        and (s.verdict or {}).get("recommendation") != "skip"
    ]
    eligible.sort(key=lambda s: s.match_score, reverse=True)
    return eligible[: settings.max_tailored_per_day]
