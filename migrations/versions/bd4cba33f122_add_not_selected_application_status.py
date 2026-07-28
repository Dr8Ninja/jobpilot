"""add not_selected application status

Revision ID: bd4cba33f122
Revises: 6221887b9442
Create Date: 2026-07-26 23:40:25.757470
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bd4cba33f122"
down_revision: str | None = "6221887b9442"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('queued', 'approved', 'applied', 'rejected', "
        "'needs_human', 'not_selected', 'failed')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM applications WHERE status = 'not_selected'")
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('queued', 'approved', 'applied', 'rejected', 'needs_human', 'failed')",
    )
