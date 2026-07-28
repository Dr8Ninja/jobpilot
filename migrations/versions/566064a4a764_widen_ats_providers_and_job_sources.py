"""widen ats providers and job sources

Revision ID: 566064a4a764
Revises: 7b363a15f5a2
Create Date: 2026-07-26 18:00:06.830846
"""

from collections.abc import Sequence

from alembic import op

revision: str = "566064a4a764"
down_revision: str | None = "7b363a15f5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CHECK constraints are the only thing standing between a typo'd source and a
    # silently mis-attributed job row, so they are widened explicitly rather than
    # dropped.
    op.drop_constraint("ck_companies_provider", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_provider",
        "companies",
        "ats_provider IS NULL OR ats_provider IN "
        "('greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters')",
    )
    op.drop_constraint("ck_jobs_source", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_source",
        "jobs",
        "source IN ('greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', "
        "'adzuna', 'remotive', 'arbeitnow', 'remoteok')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_source", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_source", "jobs", "source IN ('greenhouse', 'aggregator')")
    op.drop_constraint("ck_companies_provider", "companies", type_="check")
    op.create_check_constraint(
        "ck_companies_provider",
        "companies",
        "ats_provider IS NULL OR ats_provider IN ('greenhouse', 'lever', 'ashby')",
    )
