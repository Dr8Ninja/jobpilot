"""HTML → PDF rendering.

Two things make this module load-bearing for the no-fabrication guarantee:

1. It re-runs the whitelist gate itself rather than trusting a flag someone else
   set. The API filtering on `whitelist_passed` is the first layer; this is the
   second, and they fail independently.
2. Employer, title, and dates are read from `canonical_facts` at render time and
   never from the model's output, so a tailored bullet cannot move a job or shift
   a date even if it somehow got past the gate.
"""

import logging
import os
import pathlib
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.tailoring_io import TailoringOutput
from jobpilot_shared.whitelist import Rejected, check

log = logging.getLogger(__name__)

TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / "templates"


class GateNotPassed(RuntimeError):
    """Refused to render output that does not pass the whitelist gate."""


class RenderFailed(RuntimeError):
    """WeasyPrint could not produce a PDF."""


def _ensure_native_libraries_discoverable() -> None:
    """Make WeasyPrint's cairo/pango/glib dependencies findable on macOS.

    Homebrew installs them under /opt/homebrew/lib, which is not on the default
    dyld search path, so `ctypes.util.find_library` misses them and WeasyPrint
    fails to import. Setting the fallback path before the first import fixes it
    without requiring the user to export anything in their shell.
    """
    if sys.platform != "darwin":
        return
    for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
        if not os.path.isdir(prefix):
            continue
        current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if prefix not in current.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{prefix}:{current}" if current else prefix


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_html(
    facts: CanonicalFacts,
    output: TailoringOutput,
    *,
    target_company: str | None = None,
) -> str:
    """Render the resume HTML. Runs the gate first — this is layer two."""
    result = check(facts, output, target_company=target_company)
    if isinstance(result, Rejected):
        rules = ", ".join(sorted({v.rule for v in result.reasons}))
        raise GateNotPassed(
            f"Refusing to render: whitelist gate rejected this output ({rules}). "
            "Nothing that fails the gate may reach a PDF."
        )

    bullets_by_role: dict[int, list[str]] = {}
    for bullet in output.tailored_bullets:
        bullets_by_role.setdefault(bullet.employment_index, []).append(bullet.rewritten)

    # Only canonical skills reach the document; the gate has already proven the
    # ordered list is a subset, so this is belt-and-braces rather than a filter.
    canonical = {s.casefold(): s for s in facts.skills}
    skills = [
        canonical[s.casefold()]
        for s in output.skills_ordered_for_this_jd
        if s.casefold() in canonical
    ]
    for skill in facts.skills:
        if skill not in skills:
            skills.append(skill)

    return (
        _environment()
        .get_template("resume.html.j2")
        .render(
            facts=facts,
            output=output,
            skills=skills,
            bullets_by_role=bullets_by_role,
        )
    )


def render_pdf(
    facts: CanonicalFacts,
    output: TailoringOutput,
    destination: pathlib.Path | str,
    *,
    target_company: str | None = None,
) -> pathlib.Path:
    """Write a selectable-text, ATS-parseable PDF. Returns the path written."""
    _ensure_native_libraries_discoverable()
    from weasyprint import HTML  # imported lazily: pulls in cairo/pango

    html = build_html(facts, output, target_company=target_company)
    path = pathlib.Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        HTML(string=html).write_pdf(str(path))
    except Exception as exc:  # a render failure must not lose the tailoring run
        raise RenderFailed(f"WeasyPrint failed: {exc}") from exc
    return path
