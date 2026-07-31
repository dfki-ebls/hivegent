"""message tree

Replaces the linear ``(conversation_id, idx)`` message store with a tree: each
message has a global ``id`` and a nullable ``parent_id``.  This lets edits and
regenerations fork and preserve prior branches instead of overwriting them and
makes the database the source of truth for history.  The active branch is just
the newest one — the active path is the conversation's most recently created
message walked up to the root — so no branch pointer is stored and the
``(conversation_id, created_at)`` index serves the newest-leaf lookup.

The request/response discriminator is not stored: it already lives in the
``payload`` JSON, so the ``messagekind`` enum type is dropped here.

The project is not deployed, so this is a clean recreate of ``messages``
rather than a data migration; existing message rows are dropped.

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-06-15 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, JSONB

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_MESSAGE_KIND = ENUM("request", "response", name="messagekind", create_type=False)


def upgrade() -> None:
    op.drop_table("messages")
    # Only ``messages.kind`` referenced the enum type; drop it now that the
    # discriminator is read from the payload instead of a column.
    _MESSAGE_KIND.drop(op.get_bind())
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["messages.id"],
            name=op.f("fk_messages_parent_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        op.f("ix_messages_conversation_id_created_at"),
        "messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(op.f("ix_messages_parent_id"), "messages", ["parent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_parent_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id_created_at"), table_name="messages")
    op.drop_table("messages")
    _MESSAGE_KIND.create(op.get_bind())
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", _MESSAGE_KIND, nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "idx", name=op.f("pk_messages")),
    )
