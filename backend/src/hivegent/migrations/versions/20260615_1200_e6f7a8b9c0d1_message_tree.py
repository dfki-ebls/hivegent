"""message tree

Replaces the linear ``(conversation_id, idx)`` message store with a tree:
each message has a global ``id``, a nullable ``parent_id``, and a
``visible_prefix`` (running count of reader-visible messages from the root),
and a conversation points at the tip of the active branch via a deferrable
``active_leaf_id`` foreign key.  This lets edits and regenerations fork and
preserve prior branches instead of overwriting them, makes the database the
source of truth for history, and keeps the sidebar message count drift-free
(read from the active leaf's ``visible_prefix``) with the leaf pointer's
integrity enforced by the DB rather than by every write path.

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
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("kind", _MESSAGE_KIND, nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("visible_prefix", sa.Integer(), nullable=False),
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
        op.f("ix_messages_conversation_id"), "messages", ["conversation_id"]
    )
    op.create_index(op.f("ix_messages_parent_id"), "messages", ["parent_id"])

    op.add_column(
        "conversations", sa.Column("active_leaf_id", sa.String(), nullable=True)
    )
    # Added via ALTER (the cycle: conversations.active_leaf_id -> messages.id and
    # messages.conversation_id -> conversations.id).  DEFERRABLE INITIALLY
    # DEFERRED so a conversation and its leaf can be inserted in one transaction
    # and the pointer is validated at commit.
    op.create_foreign_key(
        op.f("fk_conversations_active_leaf_id_messages"),
        "conversations",
        "messages",
        ["active_leaf_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    # Dropping the column drops its (deferrable) FK to ``messages`` too, so the
    # table drop below is unblocked.
    op.drop_column("conversations", "active_leaf_id")

    op.drop_index(op.f("ix_messages_parent_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
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
