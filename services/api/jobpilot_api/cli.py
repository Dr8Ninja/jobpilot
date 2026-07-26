"""Typer CLI — thin wrappers over the stage functions.

Every stage is individually invocable so the tailoring prompt can be iterated in
seconds, and `run-pipeline` composes them into the nightly shape.
"""

import json
import pathlib
import subprocess
import sys

import typer
from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Profile, User
from jobpilot_shared.db.session import session_scope
from jobpilot_shared.settings import get_settings

app = typer.Typer(help="JobPilot — Phase 0 pipeline", no_args_is_help=True)

PROFILE_DIR = pathlib.Path("profile")
FACTS_PATH = PROFILE_DIR / "canonical_facts.json"


def _echo_ok(message: str) -> None:
    typer.secho(message, fg=typer.colors.GREEN)


def _echo_warn(message: str) -> None:
    typer.secho(message, fg=typer.colors.YELLOW)


@app.command()
def version() -> None:
    """Print version and the active configuration."""
    settings = get_settings()
    typer.echo("jobpilot 0.1.0 (Phase 0)")
    typer.echo(f"  database:      {settings.database_url}")
    typer.echo(f"  fixture mode:  {settings.fixture_mode}")
    typer.echo(f"  tailoring:     {settings.tailoring_model}")
    typer.echo(f"  embeddings:    {settings.embedding_model}")
    typer.echo(
        f"  dials:         threshold={settings.match_score_threshold} "
        f"max/day={settings.max_tailored_per_day} top_k={settings.embed_top_k}"
    )


@app.command("init-db")
def init_db() -> None:
    """Apply database migrations."""
    result = subprocess.run(["alembic", "upgrade", "head"], check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
    _echo_ok("Database is at head.")


@app.command("seed-companies")
def seed_companies(
    path: pathlib.Path = typer.Argument(pathlib.Path("infra/seed_companies.yaml")),
) -> None:
    """Load the Greenhouse board registry. Idempotent."""
    import yaml
    from jobpilot_worker.stages.ingest import upsert_company

    if not path.exists():
        typer.secho(f"Seed file not found: {path}", fg=typer.colors.RED)
        raise typer.Exit(1)

    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("companies", [])
    added = 0
    with session_scope() as session:
        for entry in entries:
            company = upsert_company(
                session,
                entry["name"],
                ats_provider=entry.get("ats_provider", "greenhouse"),
                board_token=entry.get("board_token"),
                discovered_via="seed",
            )
            added += 1
            typer.echo(f"  {company.name} → {company.board_token}")
    _echo_ok(f"Seeded {added} companies from {path}.")


@app.command("ingest-resume")
def ingest_resume(
    resume: pathlib.Path = typer.Argument(..., help="Path to your base resume PDF"),
) -> None:
    """Extract canonical_facts from a resume into profile/canonical_facts.json.

    Nothing is loaded into the database here — edit the JSON, then run
    `confirm-facts`. The file is gitignored; it holds your PII.
    """
    from jobpilot_worker.clients.llm import get_llm_client
    from jobpilot_worker.stages.parse_resume import ingest_resume as run_extraction

    facts = run_extraction(resume, get_llm_client())
    PROFILE_DIR.mkdir(exist_ok=True)
    FACTS_PATH.write_text(facts.model_dump_json(indent=2), encoding="utf-8")

    _echo_ok(f"Wrote {FACTS_PATH}")
    _echo_warn(
        "Review it now — this object is the whitelist every tailored resume is "
        "checked against. Correct anything the extractor got wrong, especially "
        "skills, dates, and experience_years. Then run: jobpilot confirm-facts"
    )


@app.command("confirm-facts")
def confirm_facts(
    email: str = typer.Option("owner@localhost", help="Owner email for the profile row"),
) -> None:
    """Validate profile/canonical_facts.json and load it into the database."""
    if not FACTS_PATH.exists():
        typer.secho(
            f"{FACTS_PATH} not found. Run `jobpilot ingest-resume <pdf>` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    try:
        facts = CanonicalFacts.model_validate_json(FACTS_PATH.read_text())
    except Exception as exc:
        typer.secho(f"{FACTS_PATH} is not valid canonical_facts:\n{exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    with session_scope() as session:
        from sqlalchemy import select

        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            session.add(user)
            session.flush()

        profile = session.get(Profile, user.id)
        payload = json.loads(facts.model_dump_json())
        if profile is None:
            session.add(Profile(user_id=user.id, canonical_facts=payload))
        else:
            profile.canonical_facts = payload

    _echo_ok(
        f"Confirmed: {facts.identity.name}, {facts.experience_years} years, "
        f"{len(facts.skills)} skills, {len(facts.employment)} roles."
    )


@app.command("run-pipeline")
def run_pipeline_command(
    storage: pathlib.Path = typer.Option(
        pathlib.Path("storage/resumes"), help="Where tailored PDFs are written"
    ),
) -> None:
    """Discover → dedupe → embed → score → tailor → render, in one pass."""
    from jobpilot_worker.pipeline import run_pipeline

    settings = get_settings()
    if settings.fixture_mode:
        _echo_warn("Fixture mode: using recorded data, no external API calls.")

    with session_scope() as session:
        report = run_pipeline(session, storage_dir=storage)

    typer.echo("")
    _echo_ok(report.summary())
    for note in report.notes:
        typer.echo(f"  note: {note}")


@app.command()
def discover() -> None:
    """Run discovery and dedupe only."""
    from jobpilot_worker.pipeline import PipelineReport, run_discovery

    report = PipelineReport()
    with session_scope() as session:
        run_discovery(session, report)
    _echo_ok(report.summary())
    for note in report.notes:
        typer.echo(f"  note: {note}")


@app.command()
def queue() -> None:
    """Print the current review queue."""
    from jobpilot_shared.db.models import Application, Company, Job
    from sqlalchemy import select

    with session_scope() as session:
        rows = session.execute(
            select(Application, Job, Company)
            .join(Job, Job.id == Application.job_id)
            .join(Company, Company.id == Job.company_id)
            .order_by(Application.created_at.desc())
        ).all()

        if not rows:
            _echo_warn("Queue is empty. Run `jobpilot run-pipeline`.")
            return

        for application, job, company in rows:
            typer.echo(
                f"  [{application.status:<12}] {company.name} — {job.title} "
                f"({job.location or 'n/a'}) [{job.source}/{job.description_quality}]"
            )


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    app()
