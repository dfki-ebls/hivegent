"""message ids per conversation

Keys ``messages`` by ``(conversation_id, id)`` instead of a global ``id``,
since clients choose the ids of their messages, and makes a parent lie in its
child's conversation.  Existing rows already satisfy both, so no data moves.
The downgrade restores the global key, which fails if two conversations now
share a node id.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-01 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_messages_parent_id_messages"), "messages", type_="foreignkey"
    )
    op.drop_index(op.f("ix_messages_parent_id"), table_name="messages")
    op.drop_constraint(op.f("pk_messages"), "messages", type_="primary")
    op.create_primary_key(op.f("pk_messages"), "messages", ["conversation_id", "id"])
    op.create_foreign_key(
        op.f("fk_messages_conversation_id_parent_id_messages"),
        "messages",
        "messages",
        ["conversation_id", "parent_id"],
        ["conversation_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_messages_conversation_id_parent_id"),
        "messages",
        ["conversation_id", "parent_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_conversation_id_parent_id"), table_name="messages")
    op.drop_constraint(
        op.f("fk_messages_conversation_id_parent_id_messages"),
        "messages",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("pk_messages"), "messages", type_="primary")
    op.create_primary_key(op.f("pk_messages"), "messages", ["id"])
    op.create_index(op.f("ix_messages_parent_id"), "messages", ["parent_id"])
    op.create_foreign_key(
        op.f("fk_messages_parent_id_messages"),
        "messages",
        "messages",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
