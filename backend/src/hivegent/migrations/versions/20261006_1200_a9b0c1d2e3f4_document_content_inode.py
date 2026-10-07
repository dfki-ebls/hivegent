"""document content inode

Adds the nullable ``documents.content_inode`` column beside the stat fast-path
columns, so a description replaced by another file of the same size with its
mtime preserved no longer passes for the indexed one.  Existing rows keep a
null inode, which the reconciler treats like any missing stat: one read and
hash on the next boot, and no re-embed unless the content changed.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-06 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_inode", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "content_inode")
