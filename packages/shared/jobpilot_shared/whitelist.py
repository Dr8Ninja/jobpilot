"""The anti-hallucination gate — CLAUDE.md non-negotiable #2.

A pure function. No database, no HTTP, no LLM client, no clock. Given the user's
confirmed facts and one tailoring output, it decides whether that output may be
rendered. Retry logic lives outside, in the tailoring stage.

Severities follow PRD §4.4: skills the model *declares* are a hard reject, while
technology tokens found only in prose are flagged for human review.

    check(facts, output) -> Ok | Rejected
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from .canonical_facts import CanonicalFacts
from .lexicon import MAX_LEXICON_NGRAM, TECHNOLOGY_LEXICON
from .normalize import contains_homoglyph, normalize_all, normalize_skill
from .tailoring_io import TailoringOutput

Severity = Literal["reject", "flag"]


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: Severity
    detail: str
    evidence: str


@dataclass(frozen=True)
class Ok:
    """The output may be rendered. `warnings` are flag-severity notes for the UI."""

    warnings: tuple[Violation, ...] = ()

    @property
    def passed(self) -> bool:
        return True


@dataclass(frozen=True)
class Rejected:
    """The output must not be rendered. Re-run tailoring with `reasons` as constraints."""

    reasons: tuple[Violation, ...]
    warnings: tuple[Violation, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return False


GateResult = Ok | Rejected

# "3 years", "3+ years", "3.5 yrs"
_YOE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)

# "at Google", "for Acme Corp" — a capitalised phrase in an employer-shaped position.
_EMPLOYER_PATTERN = re.compile(
    r"\b(?:at|for|joined|with)\s+((?:[A-Z][\w&.\-]*)(?:\s+[A-Z][\w&.\-]*){0,3})"
)

# Unicode-aware so a Cyrillic homoglyph cannot split a token in two and slip past
# the lexicon scan. Interior dots/hyphens are kept ("Node.js", "scikit-learn") but
# trailing sentence punctuation is not.
_WORD_PATTERN = re.compile(r"[\w+#]+(?:[.\-][\w+#]+)*")

# Capitalised words that commonly follow "at"/"for" without naming an employer.
_COMMON_CAPITALISED = frozenset(
    normalize_skill(w)
    for w in (
        "I",
        "The",
        "A",
        "An",
        "We",
        "My",
        "Our",
        "This",
        "That",
        "It",
        "They",
        "In",
        "On",
        "At",
        "For",
        "With",
        "And",
        "But",
        "Or",
        "To",
        "Of",
        "By",
        "API",
        "APIs",
        "UI",
        "UX",
        "CI",
        "CD",
        "QA",
        "PR",
        "PRs",
        "SDK",
        "SDKs",
        "CLI",
        "HTTP",
        "HTTPS",
        "JSON",
        "XML",
        "YAML",
        "CSV",
        "PDF",
        "URL",
        "URLs",
        "ID",
        "IDs",
        "MVP",
        "KPI",
        "KPIs",
        "SLA",
        "SLO",
        "SLIs",
        "RFC",
        "OKR",
        "Scale",
        "Production",
        "Runtime",
        "Startup",
        "Enterprise",
        "Engineering",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "EU",
        "US",
        "USA",
        "UK",
        "India",
        "Remote",
    )
)


def _scan_prose_for_technologies(prose: str) -> list[tuple[str, str]]:
    """Longest-match n-gram scan. Returns (normalised_key, surface_form) pairs."""
    words = _WORD_PATTERN.findall(prose)
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        for size in range(min(MAX_LEXICON_NGRAM, len(words) - i), 0, -1):
            surface = " ".join(words[i : i + size])
            key = normalize_skill(surface)
            if key and key in TECHNOLOGY_LEXICON:
                found.append((key, surface))
                i += size
                break
        else:
            i += 1
    return found


def _check_declared_skills(facts: CanonicalFacts, output: TailoringOutput) -> list[Violation]:
    allowed = normalize_all(facts.skills)
    violations: list[Violation] = []
    seen: set[str] = set()

    for skill in output.all_referenced_skills():
        key = normalize_skill(skill)
        if not key or key in seen:
            continue
        seen.add(key)

        if contains_homoglyph(skill):
            violations.append(
                Violation(
                    rule="unknown_skill",
                    severity="reject",
                    detail=(
                        f"Skill {skill!r} contains a non-Latin lookalike character. "
                        "Write skill names in plain ASCII."
                    ),
                    evidence=skill,
                )
            )
            continue

        if key not in allowed:
            violations.append(
                Violation(
                    rule="unknown_skill",
                    severity="reject",
                    detail=(
                        f"{skill!r} is not in canonical_facts.skills. The tailored resume "
                        "may only reference skills the user actually confirmed."
                    ),
                    evidence=skill,
                )
            )
    return violations


def _check_employment_indices(facts: CanonicalFacts, output: TailoringOutput) -> list[Violation]:
    violations: list[Violation] = []
    upper = len(facts.employment)
    for bullet in output.tailored_bullets:
        if not 0 <= bullet.employment_index < upper:
            violations.append(
                Violation(
                    rule="invalid_employment_index",
                    severity="reject",
                    detail=(
                        f"employment_index {bullet.employment_index} is out of range; "
                        f"canonical_facts.employment has {upper} entries."
                    ),
                    evidence=bullet.rewritten[:120],
                )
            )
    return violations


def _check_years_of_experience(facts: CanonicalFacts, output: TailoringOutput) -> list[Violation]:
    violations: list[Violation] = []
    for prose in output.all_prose():
        for match in _YOE_PATTERN.finditer(prose):
            claimed = float(match.group(1))
            if claimed > facts.experience_years + 1e-9:
                violations.append(
                    Violation(
                        rule="yoe_inflation",
                        severity="reject",
                        detail=(
                            f"Claims {claimed} years where canonical_facts.experience_years "
                            f"is {facts.experience_years}."
                        ),
                        evidence=match.group(0),
                    )
                )
    return violations


def _check_employers(
    facts: CanonicalFacts, output: TailoringOutput, target_company: str | None = None
) -> list[Violation]:
    allowed_exact = normalize_all(facts.skills) | TECHNOLOGY_LEXICON | _COMMON_CAPITALISED
    # Organisation names are prefix-matched, not exact-matched: the capitalised-run
    # regex stops at lowercase connectors, so "at Example Institute of Technology"
    # only yields "Example Institute" and must still resolve to the real institution.
    org_names = normalize_all(facts.employer_names()) | normalize_all(
        e.institution for e in facts.education
    )
    # The company being applied to is legitimately nameable — "seeking a backend
    # role at Acme" is not an employment claim. Without this the gate rejects
    # honest summaries and burns every retry on them.
    if target_company:
        org_names |= normalize_all([target_company])

    def _is_known(key: str) -> bool:
        if key in allowed_exact or key in org_names:
            return True
        return len(key) >= 3 and any(name.startswith(key) for name in org_names)

    violations: list[Violation] = []
    seen: set[str] = set()
    for prose in output.all_prose():
        for match in _EMPLOYER_PATTERN.finditer(prose):
            phrase = match.group(1)
            words = phrase.split()
            # Accept if any leading run of the phrase names something known:
            # "at Acme Corp using Redis" should match the employer "Acme Corp".
            if any(
                _is_known(normalize_skill(" ".join(words[:n]))) for n in range(len(words), 0, -1)
            ):
                continue
            key = normalize_skill(phrase)
            if key in seen:
                continue
            seen.add(key)
            violations.append(
                Violation(
                    rule="unknown_employer",
                    severity="reject",
                    detail=(
                        f"{phrase!r} is named as an organisation but does not appear in "
                        "canonical_facts.employment or education."
                    ),
                    evidence=match.group(0),
                )
            )
    return violations


def _scan_unlisted_tokens(facts: CanonicalFacts, output: TailoringOutput) -> list[Violation]:
    allowed = normalize_all(facts.skills)
    warnings: list[Violation] = []
    seen: set[str] = set()
    for prose in output.all_prose():
        for key, surface in _scan_prose_for_technologies(prose):
            if key in allowed or key in seen:
                continue
            seen.add(key)
            warnings.append(
                Violation(
                    rule="unlisted_token",
                    severity="flag",
                    detail=(
                        f"{surface!r} is a known technology that is not in "
                        "canonical_facts.skills. Confirm the phrasing does not imply "
                        "experience the user does not have."
                    ),
                    evidence=surface,
                )
            )
    return warnings


def check(
    facts: CanonicalFacts,
    output: TailoringOutput,
    *,
    target_company: str | None = None,
) -> GateResult:
    """Validate a tailoring output against the user's immutable facts.

    Returns `Rejected` if the output claims anything the facts do not support, and
    `Ok` (possibly carrying flag-severity warnings) otherwise. Callers must not
    render, persist as approvable, or display any output that does not pass.

    `target_company` is the company being applied to. Naming it is not an
    employment claim, so it is allowed in prose; omitting it makes the gate
    stricter, never looser.
    """
    rejections: list[Violation] = []
    rejections += _check_declared_skills(facts, output)
    rejections += _check_employment_indices(facts, output)
    rejections += _check_years_of_experience(facts, output)
    rejections += _check_employers(facts, output, target_company)

    warnings = tuple(_scan_unlisted_tokens(facts, output))

    if rejections:
        return Rejected(reasons=tuple(rejections), warnings=warnings)
    return Ok(warnings=warnings)


def format_violations_for_retry(reasons: tuple[Violation, ...]) -> str:
    """Render rejections as explicit constraints to append to the retry prompt."""
    lines = [
        "Your previous attempt was rejected by an automated fact-check. "
        "Fix every issue below. Do not introduce anything new.",
    ]
    lines.extend(f"- [{v.rule}] {v.detail} (found: {v.evidence!r})" for v in reasons)
    return "\n".join(lines)
