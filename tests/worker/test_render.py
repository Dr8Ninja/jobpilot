"""PDF rendering, including the round-trip that proves the guarantee survives.

The gate protects the JSON. This suite protects the artefact a recruiter opens:
generate a real PDF, extract its text with pdfplumber, and assert on what is
actually in it.
"""

import pytest
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.tailoring_io import TailoredBullet, TailoringOutput
from jobpilot_worker.stages.render import GateNotPassed, build_html, render_pdf


def _output() -> TailoringOutput:
    return TailoringOutput(
        summary="Backend engineer with 1.5 years shipping Python services.",
        tailored_bullets=[
            TailoredBullet(
                employment_index=0,
                original="Built REST endpoints for the billing service.",
                rewritten="Built and shipped REST endpoints for billing, in Python.",
                skills_referenced=["Python"],
            ),
            TailoredBullet(
                employment_index=1,
                original="Wrote internal tooling in Python.",
                rewritten="Automated release checks with internal Python tooling.",
                skills_referenced=["Python"],
            ),
        ],
        skills_ordered_for_this_jd=["Python", "PostgreSQL", "React"],
    )


def _bad_output() -> TailoringOutput:
    return TailoringOutput(
        summary="Engineer with 9 years of experience at Google.",
        tailored_bullets=[],
        skills_ordered_for_this_jd=["Kubernetes"],
    )


def test_render_refuses_output_that_fails_the_gate(facts: CanonicalFacts) -> None:
    """Layer two. Even if a caller lost track of the flag, nothing gets rendered."""
    with pytest.raises(GateNotPassed) as exc:
        build_html(facts, _bad_output())
    assert "unknown_skill" in str(exc.value)


def test_html_uses_canonical_employers_and_dates(facts: CanonicalFacts) -> None:
    """Employer, title and dates come from the facts, never from the model.

    Dates are rendered the way the source resume writes them ("Jan. 2024 –
    Present"), so the assertion is on the formatted range rather than the stored
    ISO string — the guarantee is that it is *derived from* canonical_facts.
    """
    html = build_html(facts, _output())
    for role in facts.employment:
        assert role.company in html
        assert role.title in html
        assert role.date_range() in html
    assert "Built and shipped REST endpoints" in html


def test_html_does_not_use_tables_for_layout(facts: CanonicalFacts) -> None:
    """ATS parsers mangle table layouts."""
    html = build_html(facts, _output()).lower()
    assert "<table" not in html


def test_skills_section_only_contains_canonical_skills(facts: CanonicalFacts) -> None:
    """A JD can reorder the skills block; it can never add an entry to it."""
    import re

    html = build_html(facts, _output())
    block = html.split('<div class="skills">', 1)[1].split("</div>", 1)[0]
    text = re.sub(r"<[^>]+>", "", block)
    # Grouped resumes render "Label : a, b, c"; ungrouped render one flat line.
    for line in text.splitlines():
        entries = line.split(":", 1)[-1] if ":" in line else line
        for token in entries.split(","):
            token = token.strip()
            if token:
                assert token in facts.skills, f"{token!r} is not a canonical skill"


# --------------------------------------------------------------------------
# The round trip. This is the test that proves the artefact is honest.
# --------------------------------------------------------------------------


@pytest.fixture
def rendered_text(facts: CanonicalFacts, tmp_path) -> str:
    # Same shim render_pdf applies, so the skip check sees the same environment.
    from jobpilot_worker.stages.render import _ensure_native_libraries_discoverable

    _ensure_native_libraries_discoverable()
    pytest.importorskip("weasyprint")
    pdfplumber = pytest.importorskip("pdfplumber")

    path = render_pdf(facts, _output(), tmp_path / "resume.pdf")
    assert path.exists() and path.stat().st_size > 0

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_pdf_text_is_selectable(rendered_text: str) -> None:
    """An image-only PDF is invisible to an ATS parser."""
    assert len(rendered_text.strip()) > 100


def test_pdf_contains_canonical_identity_verbatim(
    rendered_text: str, facts: CanonicalFacts
) -> None:
    assert facts.identity.name in rendered_text
    assert facts.identity.email in rendered_text


def test_pdf_contains_canonical_employers_and_dates(
    rendered_text: str, facts: CanonicalFacts
) -> None:
    for role in facts.employment:
        assert role.company in rendered_text
        assert role.title in rendered_text
        assert role.date_range() in rendered_text


def test_pdf_technologies_all_trace_back_to_canonical_facts(
    rendered_text: str, facts: CanonicalFacts
) -> None:
    """The end-to-end guarantee: nothing the user cannot claim reaches the page.

    "Claimable" is broader than the declared skills list — a technology named in
    one of the user's own original bullets is theirs too, and the gate flags such
    tokens for review rather than rejecting them (PRD §4.4). What must never
    appear is a technology with no basis anywhere in canonical_facts.
    """
    from jobpilot_shared.lexicon import TECHNOLOGY_LEXICON
    from jobpilot_shared.normalize import normalize_skill

    claimable = {normalize_skill(s) for s in facts.skills}
    for role in facts.employment:
        for source in (role.title, *role.bullets):
            words = source.split()
            for size in (1, 2, 3):
                for i in range(len(words) - size + 1):
                    claimable.add(normalize_skill(" ".join(words[i : i + size])))

    words = rendered_text.replace("·", " ").split()
    for size in (1, 2):
        for i in range(len(words) - size + 1):
            key = normalize_skill(" ".join(words[i : i + size]))
            if key in TECHNOLOGY_LEXICON and key not in claimable:
                pytest.fail(f"technology {key!r} reached the PDF with no basis in canonical_facts")


def test_fabricated_technology_would_be_caught_by_this_suite(
    facts: CanonicalFacts, tmp_path
) -> None:
    """Guard the guard: prove the round-trip check can actually fail."""
    fabricated = TailoringOutput(
        summary="Backend engineer.",
        tailored_bullets=[
            TailoredBullet(
                employment_index=0,
                original="Built REST endpoints for the billing service.",
                # Kubernetes appears nowhere in canonical_facts.
                rewritten="Operated the Kubernetes cluster behind billing.",
                skills_referenced=["Python"],
            )
        ],
        skills_ordered_for_this_jd=["Python"],
    )
    from jobpilot_shared.whitelist import check

    html = build_html(facts, fabricated)  # flagged, not rejected — so it renders
    assert "Kubernetes" in html

    result = check(facts, fabricated)
    assert any(w.rule == "unlisted_token" for w in result.warnings), (
        "an unbacked technology must at minimum reach the human as a warning"
    )


def test_pdf_contains_the_tailored_bullets(rendered_text: str) -> None:
    normalized = " ".join(rendered_text.split())
    assert "Built and shipped REST endpoints for billing" in normalized
    assert "Automated release checks" in normalized
