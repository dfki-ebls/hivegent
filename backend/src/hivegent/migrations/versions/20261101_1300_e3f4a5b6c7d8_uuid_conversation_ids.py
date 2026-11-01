"""uuid conversation ids

Rewrites every conversation and message id that is no canonical UUID into one,
since clients choose ids and only a canonical UUID is accepted, and moves each
conversation's folder from ``conversations/<id>/`` to
``conversations/<user_id>/<id>/``.  A new id derives from its old one
(UUIDv5), so a rerun after a failure maps every id and folder the same way.
Links to an old conversation id stop working, and an approval still pending
loses its staged changes.  The rewrite is one-way, so the downgrade raises.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-01 13:00:00.000000+00:00
"""

from collections.abc import Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op

from hivegent.config import is_canonical_uuid, sanitize_user_id, settings

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMESPACE = UUID("0c594661-0dd6-4be0-998c-cd180a171e59")


def _new_id(old: str) -> str:
    return old if is_canonical_uuid(old) else str(uuid5(_NAMESPACE, old))


def _move_folders(conversations: Sequence[tuple[str, str]]) -> None:
    """Move each folder below its user's under its new id, skipping one already moved."""
    root = settings.data_dir / "conversations"

    for old, user_id in conversations:
        source = root / old
        target = root / sanitize_user_id(user_id) / _new_id(old)

        if source.is_dir() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)


def upgrade() -> None:
    bind = op.get_bind()
    conversations: list[tuple[str, str]] = [
        (row.id, row.user_id)
        for row in bind.execute(sa.text("SELECT id, user_id FROM conversations"))
    ]
    messages: list[tuple[str, str]] = [
        (row.conversation_id, row.id)
        for row in bind.execute(sa.text("SELECT conversation_id, id FROM messages"))
    ]

    # Folders first, so a failed rewrite leaves them where a rerun expects them.
    _move_folders(conversations)

    conversation_map = [
        {"old": old, "new": _new_id(old)}
        for old, _ in conversations
        if not is_canonical_uuid(old)
    ]
    message_map = [
        {
            "conversation_id": conversation_id,
            "old": old,
            "new": str(uuid5(_NAMESPACE, f"{conversation_id}/{old}")),
        }
        for conversation_id, old in messages
        if not is_canonical_uuid(old)
    ]

    op.drop_constraint(
        op.f("fk_messages_conversation_id_parent_id_messages"),
        "messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_messages_conversation_id_conversations"),
        "messages",
        type_="foreignkey",
    )
    op.execute(
        "CREATE TEMP TABLE conversation_map (old text PRIMARY KEY, new text NOT NULL)"
        " ON COMMIT DROP"
    )
    op.execute(
        "CREATE TEMP TABLE message_map (conversation_id text, old text, new text NOT NULL,"
        " PRIMARY KEY (conversation_id, old)) ON COMMIT DROP"
    )

    if conversation_map:
        bind.execute(
            sa.text("INSERT INTO conversation_map VALUES (:old, :new)"), conversation_map
        )

    if message_map:
        bind.execute(
            sa.text("INSERT INTO message_map VALUES (:conversation_id, :old, :new)"),
            message_map,
        )

    # Messages are matched by their old conversation id, so they move last.
    op.execute(
        "UPDATE messages m SET parent_id = mm.new FROM message_map mm"
        " WHERE mm.conversation_id = m.conversation_id AND mm.old = m.parent_id"
    )
    op.execute(
        "UPDATE messages m SET id = mm.new FROM message_map mm"
        " WHERE mm.conversation_id = m.conversation_id AND mm.old = m.id"
    )
    op.execute(
        "UPDATE messages m SET conversation_id = cm.new FROM conversation_map cm"
        " WHERE cm.old = m.conversation_id"
    )
    op.execute(
        "UPDATE conversations c SET id = cm.new FROM conversation_map cm"
        " WHERE cm.old = c.id"
    )
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


def downgrade() -> None:
    raise RuntimeError("The rewrite of conversation ids into UUIDs is one-way")
