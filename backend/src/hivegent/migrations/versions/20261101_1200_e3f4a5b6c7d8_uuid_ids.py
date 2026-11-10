"""uuid ids

Clients now choose conversation and message ids, so every id the app mints is
a random UUID, stored in a native ``uuid`` column, and messages are keyed by
``(conversation_id, id)`` with a parent in its child's conversation.
An old id becomes its md5, so each column converts on its own and every
reference still names its row.
Retyping ``chunks`` rewrites it, which rebuilds its HNSW index.
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


def _retype(table: str, *columns: str) -> None:
    """Retype *columns* of *table* to ``uuid`` in one rewrite, each old id as its md5."""
    op.execute(
        f"ALTER TABLE {table} "
        + ", ".join(
            f"ALTER COLUMN {column} TYPE uuid USING md5({column})::uuid"
            for column in columns
        )
    )


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_messages_parent_id_messages"), "messages", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_messages_conversation_id_conversations"),
        "messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_chunks_document_id_documents"), "chunks", type_="foreignkey"
    )
    op.drop_index(op.f("ix_messages_parent_id"), table_name="messages")
    op.drop_constraint(op.f("pk_messages"), "messages", type_="primary")

    _retype("conversations", "id")
    _retype("messages", "conversation_id", "id", "parent_id")
    _retype("documents", "id")
    _retype("chunks", "id", "document_id")

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
    op.create_foreign_key(
        op.f("fk_chunks_document_id_documents"),
        "chunks",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Last, so a failed delete rolls back the rewrite.
    folders = settings.data_dir / "conversations"

    if folders.exists():
        shutil.rmtree(folders)


def downgrade() -> None:
    raise RuntimeError("The rewrite of ids into UUIDs is one-way")
