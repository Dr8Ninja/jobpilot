"""Tailoring, with the whitelist gate wrapped around it.

The gate is a pure function elsewhere; the retry loop is here. That split is
deliberate: the safety check has no I/O and can be proven exhaustively, while the
policy about what to do when it fails lives with the code that talks to the model.

After `max_tailoring_attempts` failures the run is persisted with
`whitelist_passed=False` and the application moves to `needs_human`. Failing
output is never silently shown, and never rendered.
"""

import logging
import time
from dataclasses import dataclass, field

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Event, Job, TailoringRun
from jobpilot_shared.settings import get_settings
from jobpilot_shared.tailoring_io import TailoringOutput
from jobpilot_shared.whitelist import (
    GateResult,
    Rejected,
    Violation,
    check,
    format_violations_for_retry,
)
from sqlalchemy.orm import Session

from ..clients.llm import LLMClient, LLMParseError, LLMRefusal
from ..prompts import TAILORING_SYSTEM, build_tailoring_prompt

log = logging.getLogger(__name__)


def missing_bullets(facts: CanonicalFacts, output: TailoringOutput) -> dict[int, int]:
    """Per role, how many bullets the model failed to rewrite.

    A short reply is not a fact-check failure, so the gate has nothing to say
    about it — but it is still a bad tailoring. The renderer keeps the layout
    intact by falling back to the candidate's original wording, which means a
    model that returns nothing produces a perfectly-shaped *untailored* resume.
    That looked like success until it was measured: one live reply carried zero
    bullets and still passed. So completeness is checked here and retried, using
    the same loop the gate rejections use.
    """
    counted: dict[int, int] = {}
    for bullet in output.tailored_bullets:
        if 0 <= bullet.employment_index < len(facts.employment):
            counted[bullet.employment_index] = counted.get(bullet.employment_index, 0) + 1
    return {
        index: len(role.bullets) - counted.get(index, 0)
        for index, role in enumerate(facts.employment)
        if counted.get(index, 0) < len(role.bullets)
    }


def _shortfall_constraint(facts: CanonicalFacts, shortfall: dict[int, int]) -> str:
    wanted = ", ".join(
        f"employment_index {i} needs {len(facts.employment[i].bullets)} bullets ({n} missing)"
        for i, n in sorted(shortfall.items())
    )
    return (
        f"Your previous reply was incomplete: {wanted}. Return one rewritten bullet "
        f"for EVERY bullet in canonical_facts.employment — "
        f"{sum(len(r.bullets) for r in facts.employment)} in total — in the same "
        "order, each echoing its source text in `original`. Do not drop or merge any."
    )


def _as_dicts(violations: tuple[Violation, ...]) -> list[dict]:
    return [
        {
            "rule": v.rule,
            "severity": v.severity,
            "detail": v.detail,
            "evidence": v.evidence,
        }
        for v in violations
    ]


@dataclass
class TailoringAttempt:
    output: TailoringOutput | None
    gate: GateResult | None
    attempts: int
    error: str | None = None
    history: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.gate is not None and self.gate.passed


def _completeness(facts: CanonicalFacts, output: TailoringOutput) -> int:
    """How many of the resume's bullets this output actually rewrote."""
    return sum(len(r.bullets) for r in facts.employment) - sum(
        missing_bullets(facts, output).values()
    )


def tailor_job(
    facts: CanonicalFacts,
    job: Job,
    keyword_gaps: list[str],
    client: LLMClient,
    *,
    max_attempts: int | None = None,
) -> TailoringAttempt:
    """Tailor one job, re-prompting with the gate's own rejections on failure."""
    settings = get_settings()
    max_attempts = max_attempts or settings.max_tailoring_attempts
    company = job.company.name if job.company else ""

    constraints = ""
    history: list[str] = []
    last_output: TailoringOutput | None = None
    last_gate: GateResult | None = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and settings.llm_retry_backoff_seconds > 0:
            # The provider is intermittent rather than down, so spacing the
            # attempts out is what actually recovers them. Doubling keeps the
            # first retry quick without hammering a struggling endpoint.
            delay = settings.llm_retry_backoff_seconds * (2 ** (attempt - 2))
            log.info("Waiting %.0fs before tailoring attempt %s for job %s", delay, attempt, job.id)
            time.sleep(delay)
        try:
            output = client.parse(
                model=settings.tailoring_model,
                max_tokens=settings.tailoring_max_tokens,
                system=TAILORING_SYSTEM,
                prompt=build_tailoring_prompt(
                    facts, job.title, company, job.description, keyword_gaps, constraints
                ),
                output_format=TailoringOutput,
            )
        except (LLMRefusal, LLMParseError) as exc:
            return TailoringAttempt(
                output=last_output,
                gate=last_gate,
                attempts=attempt,
                error=f"{type(exc).__name__}: {exc}",
                history=history,
            )

        gate = check(facts, output, target_company=company)
        # Keep the most complete output seen, not merely the latest: a later
        # attempt can come back emptier than an earlier one.
        if last_output is None or _completeness(facts, output) > _completeness(facts, last_output):
            last_output, last_gate = output, gate

        if gate.passed:
            shortfall = missing_bullets(facts, output)
            if not shortfall:
                return TailoringAttempt(output=output, gate=gate, attempts=attempt, history=history)
            # Passed the fact-check but left bullets untailored. Worth another
            # attempt, and harmless if the retry budget runs out — the renderer
            # falls back to the candidate's own wording for whatever is missing.
            history.append(f"attempt {attempt} incomplete: {sum(shortfall.values())} bullets short")
            log.info(
                "Job %s attempt %s passed the gate but left %s bullets untailored",
                job.id,
                attempt,
                sum(shortfall.values()),
            )
            constraints = _shortfall_constraint(facts, shortfall)
            continue

        assert isinstance(gate, Rejected)
        rules = ", ".join(sorted({v.rule for v in gate.reasons}))
        history.append(f"attempt {attempt} rejected: {rules}")
        log.info("Gate rejected job %s attempt %s (%s)", job.id, attempt, rules)
        # Feed the gate's own reasons back as explicit constraints.
        constraints = format_violations_for_retry(gate.reasons)

    return TailoringAttempt(
        output=last_output, gate=last_gate, attempts=max_attempts, history=history
    )


def persist_tailoring(session: Session, job: Job, attempt: TailoringAttempt) -> TailoringRun:
    """Record the run. `whitelist_passed` is the flag everything downstream gates on."""
    gate = attempt.gate
    run = TailoringRun(
        job_id=job.id,
        output=attempt.output.model_dump() if attempt.output else {},
        whitelist_passed=attempt.passed,
        gate_rejections=(_as_dicts(gate.reasons) if isinstance(gate, Rejected) else None),
        gate_warnings=_as_dicts(gate.warnings) if gate is not None else None,
        attempt=attempt.attempts,
    )
    session.add(run)
    session.flush()

    session.add(
        Event(
            job_id=job.id,
            type="tailoring.passed" if attempt.passed else "tailoring.needs_human",
            payload={
                "attempts": attempt.attempts,
                "history": attempt.history,
                "error": attempt.error,
            },
        )
    )
    session.flush()
    return run
