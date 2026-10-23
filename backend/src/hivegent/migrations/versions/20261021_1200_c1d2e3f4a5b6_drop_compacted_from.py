"""drop compacted from

Drops ``conversations.compacted_from_id``, the link a compaction left from the
conversation it minted to the one it summarized.  A compaction is now a message
of the conversation it compacts, so there is nothing to link.  The downgrade
restores the column empty, as no conversation continues another.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-10-21 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        op.f("ix_conversations_compacted_from_id"), table_name="conversations"
    )
    op.drop_column("conversations", "compacted_from_id")


def downgrade() -> None:
    op.add_column(
        "conversations", sa.Column("compacted_from_id", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_conversations_compacted_from_id_conversations"),
        "conversations",
        "conversations",
        ["compacted_from_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_conversations_compacted_from_id"),
        "conversations",
        ["compacted_from_id"],
        unique=False,
    )
