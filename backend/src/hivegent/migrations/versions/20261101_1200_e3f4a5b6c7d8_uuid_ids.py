"""uuid ids

Clients now choose conversation and message ids, so every existing id becomes
a random UUIDv4, the one form accepted, and messages are keyed by
``(conversation_id, id)`` with a parent in its child's conversation.
Conversation folders now lie below their user's, so the old ones are deleted.
Links to an old conversation id stop working, and every conversation loses its
``/tmp`` and the staged changes of an approval still pending.  The rewrite is
one-way, so the downgrade raises.

Revision ID: e3f4a5b6c7d8
Revises: c1d2e3f4a5b6
Create Date: 2026-11-01 12:00:00.000000+00:00
"""

import shutil
from collections.abc import Sequence

from alembic import op

from hivegent.config import settings

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Old message ids are globally unique, so one map serves ids and parents alike.
_REWRITE = """
WITH conversation_ids AS MATERIALIZED (
    SELECT id AS old, gen_random_uuid()::text AS new FROM conversations
), message_ids AS MATERIALIZED (
    SELECT id AS old, gen_random_uuid()::text AS new FROM messages
), rewritten_conversations AS (
    UPDATE conversations c SET id = ci.new
    FROM conversation_ids ci WHERE ci.old = c.id
)
UPDATE messages m SET id = mi.new, parent_id = pi.new, conversation_id = ci.new
FROM messages o
JOIN message_ids mi ON mi.old = o.id
JOIN conversation_ids ci ON ci.old = o.conversation_id
LEFT JOIN message_ids pi ON pi.old = o.parent_id
WHERE m.id = o.id
"""


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_messages_parent_id_messages"), "messages", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_messages_conversation_id_conversations"),
        "messages",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_messages_parent_id"), table_name="messages")
    op.drop_constraint(op.f("pk_messages"), "messages", type_="primary")
    op.execute(_REWRITE)
    op.create_primary_key(op.f("pk_messages"), "messages", ["conversation_id", "id"])
    op.create_foreign_key(
        op.f("fk_messages_conversation_id_conversations"),
        "messages",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
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

    # Last, so a failed delete rolls back the rewrite.
    folders = settings.data_dir / "conversations"

    if folders.exists():
        shutil.rmtree(folders)


def downgrade() -> None:
    raise RuntimeError("The rewrite of ids into UUIDs is one-way")
