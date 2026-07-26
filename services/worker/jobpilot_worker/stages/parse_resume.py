"""Resume → canonical_facts extraction.

Two-step by design: the LLM *proposes*, the user *confirms*. Nothing downstream
reads the proposal — the whitelist gate validates against the confirmed object
only, so an extraction mistake becomes a review-time correction rather than a
fabricated resume.
"""

import logging
import pathlib

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.settings import get_settings

from ..clients.llm import LLMClient

log = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """\
You extract a structured facts object from a resume.

Copy what the resume says. Do not infer, round, upgrade, or tidy:
- experience_years: compute from the employment dates actually listed. If the
  resume states a figure, use that figure.
- skills: only technologies the resume explicitly names.
- employment: preserve company names, titles, and dates exactly as written.
- bullets: copy each bullet's text; do not rewrite them.

If a field is absent from the resume, leave it null or empty rather than guessing.
This object becomes an immutable whitelist — anything you invent here silently
becomes permission to lie later.
"""


def extract_text(pdf_path: pathlib.Path | str) -> str:
    """Layout-aware text extraction."""
    import pdfplumber

    path = pathlib.Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"Resume not found: {path}")

    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8")

    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError(
            f"No selectable text in {path.name}. If this is a scanned resume, "
            "export a text-based PDF — an ATS cannot read it either."
        )
    return text


def extract_facts(resume_text: str, client: LLMClient) -> CanonicalFacts:
    settings = get_settings()
    return client.parse(
        model=settings.extraction_model,
        max_tokens=settings.extraction_max_tokens,
        system=EXTRACTION_SYSTEM,
        prompt=f"<resume>\n{resume_text[:40000]}\n</resume>\n\nExtract the facts object.",
        output_format=CanonicalFacts,
    )


def ingest_resume(pdf_path: pathlib.Path | str, client: LLMClient) -> CanonicalFacts:
    return extract_facts(extract_text(pdf_path), client)
