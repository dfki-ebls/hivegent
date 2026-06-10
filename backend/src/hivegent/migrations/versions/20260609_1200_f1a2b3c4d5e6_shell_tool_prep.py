"""shell-tool prep: imported origin + content fingerprint columns

Renames ``documents.content_sha256`` to ``documents.content_digest`` (the
column was never populated, so the rename has no data impact), adds the
``content_mtime_ns`` / ``content_size`` stat fast-path columns the reconciler
uses to skip re-reading unchanged descriptions, and adds the ``imported``
value to the ``origin`` enum for entries the reconciler folds in from disk
without a prior row.

Revision ID: f1a2b3c4d5e6
Revises: e5a1b2c3d4f5
Create Date: 2026-06-09 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e5a1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("documents", "content_sha256", new_column_name="content_digest")
    op.add_column(
        "documents", sa.Column("content_mtime_ns", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("content_size", sa.BigInteger(), nullable=True)
    )
    # ``ADD VALUE`` is allowed inside Alembic's transaction on PostgreSQL 12+
    # as long as the new label is not used in the same transaction.
    op.execute("ALTER TYPE origin ADD VALUE IF NOT EXISTS 'imported'")


def downgrade() -> None:
    op.drop_column("documents", "content_size")
    op.drop_column("documents", "content_mtime_ns")
    op.alter_column("documents", "content_digest", new_column_name="content_sha256")
    # PostgreSQL cannot drop an enum value; recreating the type to remove
    # ``imported`` would require rewriting every column that references it, so
    # the value is intentionally left in place on downgrade.
