"""The gate's retry loop: a rejected attempt must be re-prompted, not shipped."""

from dataclasses import dataclass

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.settings import get_settings
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from jobpilot_worker.clients.llm import FakeLLMClient, LLMRefusal
from jobpilot_worker.stages.tailor import tailor_job


@dataclass
class StubCompany:
    name: str = "Target Co"


@dataclass
class StubJob:
    id: int = 1
    title: str = "Backend Engineer"
    description: str = "Python, PostgreSQL, and Redis. Kubernetes a plus."
    company: StubCompany = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.company is None:
            self.company = StubCompany()


def _clean(facts: CanonicalFacts) -> TailoringOutput:
    return TailoringOutput(
        summary="Backend engineer with 1.5 years shipping Python services.",
        tailored_bullets=[
            TailoredBullet(
                employment_index=0,
                original="Built REST endpoints for the billing service.",
                rewritten="Built REST endpoints for the billing service in Python.",
                skills_referenced=["Python"],
            )
        ],
        skills_ordered_for_this_jd=["Python", "PostgreSQL", "Redis"],
    )


def _hallucinated() -> TailoringOutput:
    return TailoringOutput(
        summary="Engineer with 6 years of experience.",
        tailored_bullets=[
            TailoredBullet(
                employment_index=0,
                original="Built REST endpoints for the billing service.",
                rewritten="Ran the Kubernetes platform at Netflix.",
                skills_referenced=["Kubernetes"],
            )
        ],
        skills_ordered_for_this_jd=["Kubernetes"],
    )


def test_clean_output_passes_on_the_first_attempt(facts: CanonicalFacts) -> None:
    client = FakeLLMClient()
    client.queue(_clean(facts))

    attempt = tailor_job(facts, StubJob(), ["Kubernetes"], client)

    assert attempt.passed
    assert attempt.attempts == 1
    assert len(client.calls) == 1


def test_rejected_output_is_retried_with_the_gate_reasons(facts: CanonicalFacts) -> None:
    client = FakeLLMClient()
    client.queue(_hallucinated(), _clean(facts))

    attempt = tailor_job(facts, StubJob(), [], client)

    assert attempt.passed, "the second attempt should satisfy the gate"
    assert attempt.attempts == 2
    assert len(client.calls) == 2

    retry_prompt = client.calls[1]["prompt"]
    assert "fact_check_failures" in retry_prompt
    for expected in ("unknown_skill", "yoe_inflation", "unknown_employer"):
        assert expected in retry_prompt, f"{expected} should be fed back to the model"
    assert "Netflix" in retry_prompt


def test_persistently_hallucinating_model_is_never_shipped(facts: CanonicalFacts) -> None:
    """After the attempt budget the run fails closed — it does not degrade to 'ok'."""
    client = FakeLLMClient()
    client.queue(_hallucinated())

    attempt = tailor_job(facts, StubJob(), [], client, max_attempts=3)

    assert not attempt.passed
    assert attempt.attempts == 3
    assert len(client.calls) == 3
    assert attempt.output is not None, "the last attempt is kept for human inspection"


def test_refusal_stops_the_loop_without_shipping(facts: CanonicalFacts) -> None:
    class Refusing:
        def parse(self, **kwargs):
            raise LLMRefusal("declined")

    attempt = tailor_job(facts, StubJob(), [], Refusing())
    assert not attempt.passed
    assert attempt.error is not None and "LLMRefusal" in attempt.error


def test_prompt_carries_the_canonical_whitelist(facts: CanonicalFacts) -> None:
    client = FakeLLMClient()
    client.queue(_clean(facts))
    tailor_job(facts, StubJob(), ["gRPC"], client)

    prompt = client.calls[0]["prompt"]
    assert "canonical_facts" in prompt
    assert "Acme Corp" in prompt
    assert "gRPC" in prompt, "keyword gaps are passed as context"


def test_no_sampling_parameters_are_ever_sent(facts: CanonicalFacts) -> None:
    """temperature/top_p/top_k are rejected with a 400 on Claude Sonnet 5."""
    client = FakeLLMClient()
    client.queue(_clean(facts))
    tailor_job(facts, StubJob(), [], client)

    call = client.calls[0]
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in call
    assert call["model"] == get_settings().tailoring_model
