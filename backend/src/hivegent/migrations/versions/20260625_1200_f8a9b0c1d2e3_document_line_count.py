"""document line count

Adds the non-null ``documents.line_count`` column: the markdown's line count,
written eagerly at row insert (a pure function of the content) and surfaced
through a batch endpoint so the frontend coverage map can place a partial read
against the whole document.

Pre-existing rows are seeded with a transient ``0`` server default (then the
default is dropped) since a SQL migration cannot read the workspace files to
backfill real counts; those rows pick up an accurate count on their next
re-index, and a ``0`` placeholder is treated as "unknown" by the query.

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-06-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("documents", "line_count", server_default=None)


def downgrade() -> None:
    op.drop_column("documents", "line_count")
