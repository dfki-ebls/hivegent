"""uuid ids and deferred requests

Clients now choose conversation and message ids, so every id the app mints is
a random UUID, stored in a native ``uuid`` column, and messages are keyed by
``(conversation_id, id)`` with a parent in its child's conversation.
An old id becomes its md5, so each column converts on its own and every
reference still names its row.
Retyping ``chunks`` rewrites it, which rebuilds its HNSW index.

A run that pauses now stores its ``DeferredToolRequests`` on its last response
under ``deferredToolRequests``, which the next request resumes against, so a
turn still awaiting an approval or an answer gets them from its unanswered
calls and its ``approvalMetadata``, which stays for the client.

Conversation folders now lie below their user's, so the old ones are deleted.
Links to an old conversation id stop working, and every conversation loses its
``/tmp`` and the staged changes of an approval still pending.  The rewrite is
one-way, so the downgrade raises.

Revision ID: e3f4a5b6c7d8
Revises: c1d2e3f4a5b6
Create Date: 2026-11-01 12:00:00.000000+00:00
"""

import json
import shutil
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from hivegent.config import settings

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFERRED = "deferredToolRequests"
_APPROVAL_METADATA = "approvalMetadata"
_ASK_USER = "ask_user"

# A paused run ends on its response, or on the request answering the calls it
# ran beside the deferred ones, so only a leaf and its parent can hold one.
_LEAVES = """
SELECT l.conversation_id, l.id, l.payload, p.id AS parent_id, p.payload AS parent
FROM messages l
LEFT JOIN messages p ON p.conversation_id = l.conversation_id AND p.id = l.parent_id
WHERE NOT EXISTS (
    SELECT 1 FROM messages c
    WHERE c.conversation_id = l.conversation_id AND c.parent_id = l.id
)
"""

_SET_PAYLOAD = sa.text(
    "UPDATE messages SET payload = CAST(:payload AS jsonb) "
    "WHERE conversation_id = :conversation_id AND id = :id"
)


def paused[K](
    leaf_id: K,
    leaf: Mapping[str, Any],
    parent_id: K | None,
    parent: Mapping[str, Any] | None,
) -> tuple[K, dict[str, Any]] | None:
    """The response the branch ending on *leaf* paused on, its requests stored.

    Returns the id of that response and its new payload, or ``None`` when the
    branch awaits nothing.
    """
    if leaf["kind"] == "response":
        response_id, response, answered = leaf_id, leaf, set[str]()

    elif (
        parent_id is not None
        and parent is not None
        and parent["kind"] == "response"
        and leaf["parts"]
        and all(part["part_kind"] == "tool-return" for part in leaf["parts"])
    ):
        response_id, response = parent_id, parent
        answered = {part["tool_call_id"] for part in leaf["parts"]}

    else:
        return None

    pending = [
        part
        for part in response["parts"]
        if part["part_kind"] == "tool-call" and part["tool_call_id"] not in answered
    ]

    if not pending:
        return None

    metadata = dict(response.get("metadata") or {})
    approval_metadata = metadata.get(_APPROVAL_METADATA) or {}
    approvals = [part for part in pending if part["tool_name"] != _ASK_USER]
    metadata[_DEFERRED] = {
        "calls": [part for part in pending if part["tool_name"] == _ASK_USER],
        "approvals": approvals,
        "metadata": {
            part["tool_call_id"]: approval_metadata[part["tool_call_id"]]
            for part in approvals
            if part["tool_call_id"] in approval_metadata
        },
    }

    return response_id, {**response, "metadata": metadata}


def _store_deferred_requests() -> None:
    """Store the requests every paused turn awaits, as a run that pauses now does."""
    bind = op.get_bind()

    for row in bind.execute(sa.text(_LEAVES)).mappings().all():
        if (
            found := paused(row["id"], row["payload"], row["parent_id"], row["parent"])
        ) is None:
            continue

        response_id, payload = found
        bind.execute(
            _SET_PAYLOAD,
            {
                "payload": json.dumps(payload),
                "conversation_id": row["conversation_id"],
                "id": response_id,
            },
        )


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

    _store_deferred_requests()

    # Last, so a failed delete rolls back the rewrite.
    folders = settings.data_dir / "conversations"

    if folders.exists():
        shutil.rmtree(folders)


def downgrade() -> None:
    raise RuntimeError("The rewrite of ids into UUIDs is one-way")
