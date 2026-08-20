"""add user_id to applications

The `users` table has existed since the first migration and nothing read it.
This is the column that makes it load-bearing.

Nullable on purpose. Every existing row is backfilled to the single owner, so
after this runs there should be no NULLs at all — but the column stays nullable
so that a row written by some path that has not been updated is *unowned* rather
than rejected, and unowned rows stay visible to the owner. Nothing in this
system is ever deleted, and a card dropping out of the queue would be a deletion
in all but name.

Revision ID: e417026e4b24
Revises: fd29c97f7287
Create Date: 2026-08-20 22:31:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e417026e4b24"
down_revision: str | None = "fd29c97f7287"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_applications_user_id"), "applications", ["user_id"], unique=False)
    op.create_foreign_key(
        "fk_applications_user",
        "applications",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Backfill. This is a single-user installation, so "the owner" is the lowest
    # user id; `owner_email` is only needed once there is more than one, which
    # there is not yet. If the table is empty — a fresh database with no
    # `confirm-facts` run — this updates nothing, which is correct.
    op.execute(
        """
        UPDATE applications
           SET user_id = (SELECT MIN(id) FROM users)
         WHERE user_id IS NULL
           AND EXISTS (SELECT 1 FROM users)
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_applications_user", "applications", type_="foreignkey")
    op.drop_index(op.f("ix_applications_user_id"), table_name="applications")
    op.drop_column("applications", "user_id")
