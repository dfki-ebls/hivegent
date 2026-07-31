"""add video entry kind

Uploaded videos (MP4, WebM, MOV, MKV) become first-class entries: the
original file plus a vision-generated markdown description built from
frames sampled across the timeline, mirroring how image entries work.

``ALTER TYPE ... ADD VALUE`` is irreversible in PostgreSQL (values can
only be added), so the downgrade is a no-op; pre-existing rows are
unaffected either way.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-11 15:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE entrykind ADD VALUE IF NOT EXISTS 'video'")


def downgrade() -> None:
    pass
