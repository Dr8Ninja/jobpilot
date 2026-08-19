"""The cross-source dedupe rule must be enforced by the database, not by hope.

The user's rule: drop the aggregator row **only** when we are certain it is the
same job — proven by a matching Greenhouse job id. These tests pin that certainty
requirement to the partial unique index that implements it.
"""

import pytest
from jobpilot_shared.db.models import Company, Job
from sqlalchemy.exc import IntegrityError


def _company(db, name: str = "Acme Corp", token: str | None = "acme") -> Company:
    company = Company(
        name=name,
        normalized_name=name.lower().replace(" ", ""),
        ats_provider="greenhouse" if token else None,
        board_token=token,
        discovered_via="seed",
    )
    db.add(company)
    db.flush()
    return company


def _job(company: Company, *, source: str, external_id: str, ats_job_id: str | None) -> Job:
    return Job(
        company_id=company.id,
        source=source,
        external_id=external_id,
        ats_job_id=ats_job_id,
        title="Software Engineer",
        location="Remote",
        description="Build things.",
        apply_url="https://example.invalid/apply",
        content_hash="deadbeef",
    )


def test_same_source_and_external_id_cannot_duplicate(db) -> None:
    """Re-running discovery must be idempotent."""
    company = _company(db)
    db.add(_job(company, source="greenhouse", external_id="1", ats_job_id="1"))
    db.flush()

    db.add(_job(company, source="greenhouse", external_id="1", ats_job_id="1"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_certain_duplicate_is_blocked_by_the_database(db) -> None:
    """A Greenhouse row and a resolved aggregator row sharing a real job id."""
    company = _company(db)
    db.add(_job(company, source="greenhouse", external_id="gh-1", ats_job_id="4012345"))
    db.flush()

    db.add(_job(company, source="adzuna", external_id="adz-99", ats_job_id="4012345"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_unresolved_aggregator_rows_coexist(db) -> None:
    """NULL ats_job_id means 'not certain' — those must never be collapsed.

    Postgres treats NULLs as distinct in a unique index, which is exactly the
    behaviour the certainty rule needs.
    """
    company = _company(db)
    for i in range(3):
        db.add(_job(company, source="adzuna", external_id=f"adz-{i}", ats_job_id=None))
    db.flush()

    assert db.query(Job).filter(Job.ats_job_id.is_(None)).count() == 3


def test_same_ats_job_id_across_companies_is_allowed(db) -> None:
    """Greenhouse job ids are only unique within a board."""
    acme = _company(db, "Acme Corp", "acme")
    beta = _company(db, "Beta Labs", "beta")
    db.add(_job(acme, source="greenhouse", external_id="a-1", ats_job_id="777"))
    db.add(_job(beta, source="greenhouse", external_id="b-1", ats_job_id="777"))
    db.flush()

    assert db.query(Job).filter(Job.ats_job_id == "777").count() == 2


def test_companies_without_a_board_token_coexist(db) -> None:
    """Aggregator-discovered companies may have no resolvable ATS board."""
    for name in ("No Board One", "No Board Two"):
        db.add(
            Company(
                name=name,
                normalized_name=name.lower().replace(" ", ""),
                ats_provider=None,
                board_token=None,
                discovered_via="aggregator",
            )
        )
    db.flush()
    assert db.query(Company).filter(Company.board_token.is_(None)).count() == 2


def test_duplicate_board_token_is_blocked(db) -> None:
    _company(db, "Acme Corp", "acme")
    db.add(
        Company(
            name="Acme Corporation",
            normalized_name="acmecorporation",
            ats_provider="greenhouse",
            board_token="acme",
            discovered_via="aggregator",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_superseded_by_records_the_drop_instead_of_deleting(db) -> None:
    """Redundancy must stay measurable — we record, we do not delete."""
    company = _company(db)
    winner = _job(company, source="greenhouse", external_id="gh-1", ats_job_id="4012345")
    loser = _job(company, source="adzuna", external_id="adz-99", ats_job_id=None)
    db.add_all([winner, loser])
    db.flush()

    loser.superseded_by = winner.id
    db.flush()

    assert db.query(Job).count() == 2
    assert db.get(Job, loser.id).superseded_by == winner.id


def test_application_status_check_constraint(db) -> None:
    from jobpilot_shared.db.models import Application

    company = _company(db)
    job = _job(company, source="greenhouse", external_id="gh-1", ats_job_id="1")
    db.add(job)
    db.flush()

    db.add(Application(job_id=job.id, status="not_a_real_status"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_the_seed_file_may_correct_a_stale_board_token(db) -> None:
    """Companies migrate ATS vendors, and the seed file is the verified registry.

    Fireworks AI moved its Ashby slug from `fireworksai` to `fireworks`. Before
    this, re-running `seed-companies` reported success and changed nothing, so
    the dead token 404'd on every run forever.
    """
    from jobpilot_worker.stages.ingest import upsert_company

    first = upsert_company(
        db,
        "Fireworks AI",
        ats_provider="ashby",
        board_token="fireworksai",
        discovered_via="seed",
    )
    assert first.board_token == "fireworksai"

    second = upsert_company(
        db,
        "Fireworks AI",
        ats_provider="ashby",
        board_token="fireworks",
        discovered_via="seed",
    )
    assert second.id == first.id, "the company must be updated, not duplicated"
    assert second.board_token == "fireworks"


def test_an_aggregator_guess_never_overwrites_a_verified_token(db) -> None:
    """The conservative half of the rule: only the seed file is authoritative."""
    from jobpilot_worker.stages.ingest import upsert_company

    upsert_company(
        db,
        "Acme Corp",
        ats_provider="greenhouse",
        board_token="acme",
        discovered_via="seed",
    )
    guessed = upsert_company(
        db,
        "Acme Corp",
        ats_provider="lever",
        board_token="acme-guess",
        discovered_via="aggregator",
    )
    assert guessed.board_token == "acme"
    assert guessed.ats_provider == "greenhouse"


def test_the_aggregator_may_still_supply_a_missing_token(db) -> None:
    """Registry growth is the reason the aggregator was pulled into Phase 0."""
    from jobpilot_worker.stages.ingest import upsert_company

    upsert_company(db, "Beta Inc", discovered_via="aggregator")
    grown = upsert_company(
        db,
        "Beta Inc",
        ats_provider="greenhouse",
        board_token="beta",
        discovered_via="aggregator",
    )
    assert grown.board_token == "beta"
