"""rename documents.original_ext to original_suffix

The column now stores the original file's pathlib suffix *with* its leading dot
(``.pdf``) instead of the bare extension (``pdf``), and an empty string marks an
extension-less or dotfile original (path equals the stem) distinctly from
``NULL`` (no original).  The database was reset for this change, so the rename
carries no data and needs no value backfill.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-06-09 13:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("documents", "original_ext", new_column_name="original_suffix")


def downgrade() -> None:
    op.alter_column("documents", "original_suffix", new_column_name="original_ext")
