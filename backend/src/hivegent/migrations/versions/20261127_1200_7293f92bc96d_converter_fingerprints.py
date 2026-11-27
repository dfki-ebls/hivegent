"""converter fingerprints

A document stamps the fingerprint of the converter of its markdown in ``converter``.

Revision ID: 7293f92bc96d
Revises: b027c240705a
Create Date: 2026-11-27 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7293f92bc96d"
down_revision: str | Sequence[str] | None = "b027c240705a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("converter", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "converter")
