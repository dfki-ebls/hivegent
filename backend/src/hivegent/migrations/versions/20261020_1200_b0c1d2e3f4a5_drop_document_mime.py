"""drop document mime

Drops ``documents.mime``, a pure function of the entry's paths that
``converters.formats.media_type`` now derives on read, so rows stored with the
host registry's guess no longer disagree with what is served.  The downgrade
restores the column empty, which the previous revision's code tolerates, since
nothing reads it but the API field it derives anyway.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-10-20 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("documents", "mime")


def downgrade() -> None:
    op.add_column("documents", sa.Column("mime", sa.String(), nullable=True))
