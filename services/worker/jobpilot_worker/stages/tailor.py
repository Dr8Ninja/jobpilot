"""Tailoring, with the whitelist gate wrapped around it.

The gate is a pure function elsewhere; the retry loop is here. That split is
deliberate: the safety check has no I/O and can be proven exhaustively, while the
policy about what to do when it fails lives with the code that talks to the model.

After `max_tailoring_attempts` failures the run is persisted with
`whitelist_passed=False` and the application moves to `needs_human`. Failing
output is never silently shown, and never rendered.
"""

import logging
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
        last_output, last_gate = output, gate

        if gate.passed:
            return TailoringAttempt(output=output, gate=gate, attempts=attempt, history=history)

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
