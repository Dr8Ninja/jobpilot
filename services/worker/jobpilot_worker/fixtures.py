"""Fixture mode — the real pipeline, no API keys.

`JOBPILOT_FIXTURE_MODE=1` swaps the live clients for these. Every stage, the
whitelist gate, the PDF renderer, the database writes, and the dashboard run
exactly as they do in production; only the three external calls are replaced.

The fixture LLM reads canonical_facts back out of the prompt and answers using
only what it finds there, so its tailoring output passes the gate honestly rather
than because the gate was bypassed.
"""

import hashlib
import json
import re
from typing import TypeVar

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.scoring_io import ScoreVerdict
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_FACTS_BLOCK = re.compile(r"<canonical_facts>\s*(\{.*?\})\s*</canonical_facts>", re.DOTALL)
_TITLE_LINE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_COMPANY_LINE = re.compile(r"^Company:\s*(.+)$", re.MULTILINE)

SAMPLE_FACTS = CanonicalFacts.model_validate(
    {
        "identity": {
            "name": "Sample Candidate",
            "email": "sample@example.invalid",
            "phone": "+91-00000-00000",
            "location": "Bengaluru, India",
        },
        "links": {
            "linkedin": "https://linkedin.com/in/sample",
            "github": "https://github.com/sample",
        },
        "experience_years": 1.5,
        "skills": [
            "Python",
            "JavaScript",
            "TypeScript",
            "React",
            "Node.js",
            "PostgreSQL",
            "Redis",
            "Docker",
            "FastAPI",
            "Git",
            "REST",
        ],
        "employment": [
            {
                "company": "Acme Corp",
                "title": "Software Engineer",
                "start": "2024-01",
                "end": "present",
                "bullets": [
                    "Built and maintained REST endpoints for the billing service.",
                    "Reduced p95 latency on the search path by adding a Redis cache.",
                    "Containerised the service with Docker for local development.",
                ],
            },
            {
                "company": "Beta Labs",
                "title": "Software Engineer Intern",
                "start": "2023-06",
                "end": "2023-12",
                "bullets": [
                    "Wrote internal tooling in Python to automate release checks.",
                    "Added integration tests covering the payments flow.",
                ],
            },
        ],
        "education": [
            {
                "degree": "B.Tech Computer Science",
                "institution": "Example Institute of Technology",
                "year": "2023",
            }
        ],
    }
)


def _stable_int(text: str, low: int, high: int) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return low + (int.from_bytes(digest[:4], "big") % (high - low + 1))


def _facts_from_prompt(prompt: str) -> CanonicalFacts:
    match = _FACTS_BLOCK.search(prompt)
    if not match:
        return SAMPLE_FACTS
    try:
        return CanonicalFacts.model_validate(json.loads(match.group(1)))
    except Exception:
        return SAMPLE_FACTS


def _relevant_skills(facts: CanonicalFacts, jd: str) -> list[str]:
    lowered = jd.lower()
    hits = [s for s in facts.skills if s.lower() in lowered]
    rest = [s for s in facts.skills if s not in hits]
    return hits + rest


class FixtureLLMClient:
    """Deterministic stand-in for Claude. Same Protocol as `AnthropicLLMClient`."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def parse(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        prompt: str,
        output_format: type[T],
    ) -> T:
        self.calls.append({"model": model, "output_format": output_format})

        if output_format is ScoreVerdict:
            return self._score(prompt)  # type: ignore[return-value]
        if output_format is TailoringOutput:
            return self._tailor(prompt)  # type: ignore[return-value]
        if output_format is CanonicalFacts:
            return SAMPLE_FACTS  # type: ignore[return-value]
        raise AssertionError(f"Fixture mode has no canned {output_format.__name__} response")

    def _score(self, prompt: str) -> ScoreVerdict:
        facts = _facts_from_prompt(prompt)
        title = (_TITLE_LINE.search(prompt) or [None, ""])[1].strip()
        jd = prompt.lower()

        matched = [s for s in facts.skills if s.lower() in jd]
        # Seniority is the dominant signal, mirroring the real prompt's instruction.
        senior_markers = ("staff", "principal", "lead", "senior", "8+ years", "10+ years")
        is_stretch = any(marker in (title + " " + jd) for marker in senior_markers)

        base = 55 + min(len(matched), 6) * 6
        score = max(10, base - 40) if is_stretch else min(97, base + _stable_int(title, 0, 8))

        return ScoreVerdict(
            match_score=score,
            must_have_coverage=[f"{s}: met" for s in matched[:6]],
            keyword_gaps=[
                token
                for token in ("Kubernetes", "AWS", "gRPC", "Kafka")
                if token.lower() in jd and token not in facts.skills
            ],
            seniority_fit="stretch" if is_stretch else "good",
            recommendation="skip" if is_stretch else "tailor",
            rationale=(
                "Seniority is above the candidate's experience; skipping."
                if is_stretch
                else f"Overlaps on {', '.join(matched[:4]) or 'general backend work'}."
            ),
        )

    def _tailor(self, prompt: str) -> TailoringOutput:
        facts = _facts_from_prompt(prompt)
        title = (_TITLE_LINE.search(prompt) or [None, "the role"])[1].strip()
        company = (_COMPANY_LINE.search(prompt) or [None, "the company"])[1].strip()
        ordered = _relevant_skills(facts, prompt)

        bullets: list[TailoredBullet] = []
        for index, role in enumerate(facts.employment):
            for original in role.bullets:
                emphasis = next((s for s in ordered if s.lower() in original.lower()), ordered[0])
                bullets.append(
                    TailoredBullet(
                        employment_index=index,
                        original=original,
                        # Rephrased, never invented — the source bullet is preserved.
                        rewritten=original.rstrip(".") + f", with {emphasis}.",
                        skills_referenced=[emphasis],
                    )
                )

        return TailoringOutput(
            summary=(
                f"{facts.employment[0].title if facts.employment else 'Software engineer'} "
                f"with {facts.experience_years} years, targeting {title} at {company}."
            ),
            tailored_bullets=bullets,
            skills_ordered_for_this_jd=ordered,
        )


def build_fake_llm_client() -> FixtureLLMClient:
    return FixtureLLMClient()


# ---------------------------------------------------------------------------
# Fixture HTTP: recorded API responses so discovery runs without credentials.
# ---------------------------------------------------------------------------

FIXTURE_DATA = __import__("pathlib").Path(__file__).parent / "fixture_data"


def fixture_fetch(url: str, **kwargs):
    """Stand-in for `clients.http.fetch`, serving recorded payloads.

    Unknown URLs raise rather than returning empty: a silent no-op would look
    exactly like "the board had no jobs today", which is the wrong signal when
    you are trying to see the pipeline work.
    """
    from .clients.http import FetchResult

    if "boards-api.greenhouse.io" in url:
        return FetchResult(
            url=url,
            status=200,
            text=(FIXTURE_DATA / "greenhouse_board.json").read_text(),
            final_url=url,
        )
    if "api.adzuna.com" in url:
        return FetchResult(
            url=url,
            status=200,
            text=(FIXTURE_DATA / "adzuna_search.json").read_text(),
            final_url=url,
        )
    if "adzuna.in/land/ad/4900000001" in url:
        # This aggregator hit resolves to a Greenhouse job we already have, which
        # is what exercises the certainty dedupe path end to end.
        return FetchResult(
            url=url,
            status=200,
            text="<html></html>",
            final_url="https://boards.greenhouse.io/acme/jobs/4012345",
        )
    if "adzuna.in/land/ad/" in url:
        return FetchResult(
            url=url, status=200, text="<html></html>", final_url="https://example.invalid/careers"
        )
    raise AssertionError(f"Fixture mode has no recorded response for {url}")
