"""conversation store

Conversations are now kept by a pydantic-ai ``ConversationStore``: a
``conversations`` row is its record and a ``messages`` row an entry.  The
record names its active history by ``head_id`` rather than by the newest leaf,
the histories it left as ``branches``, carries the calls a paused run awaits,
the usage, and a ``revision`` a save checks.  Messages are never moved again,
and their ids are minted by the store, so every id is replaced by a fresh one
and messages are keyed by it alone.

The id a client knows a message by now rides in the message's metadata, where
pydantic-ai's UI adapters keep it: a prompt keeps its old id, every message of
a reply the id of the reply's first message, and a compaction stored ahead of
a prompt its own.  The paused requests a response carried move to the record.
Conversations keep their ids, so links keep working.

pydantic-ai stamps the whole system prompt on every request, so the record
keeps the prompt of the conversation's first request once, as ``instructions``,
and a request sent under it stores none.

The return of a call that delegated to a subagent stored the subagent's whole
history as a ``data-subagent-history`` part, while the client renders only its
steps, so each such part becomes the ``data-tool-output`` transcript of those
steps a turn now stores.

Every message is rewritten once, a conversation at a time.  The rewrite is
one-way, so the downgrade raises.

Revision ID: 65f6705faca2
Revises: e3f4a5b6c7d8
Create Date: 2026-11-23 12:00:00.000000+00:00
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "65f6705faca2"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFERRED = "deferredToolRequests"
_HISTORY = "data-subagent-history"
_NAMESPACE = "__pydantic_ai__"
_UI_MESSAGE_ID = "ui_message_id"


@dataclass(frozen=True, slots=True)
class Node:
    """A stored message as the tree held it."""

    id: UUID
    parent_id: UUID | None
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Entry:
    """A message as the store keeps it, under its new id."""

    old_id: UUID
    id: UUID
    parent_id: UUID | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Record:
    """A conversation's record and its entries."""

    head_id: UUID | None
    branches: list[UUID]
    deferred_tool_requests: dict[str, Any] | None
    instructions: str | None
    entries: list[Entry]


def _is_prompt(payload: Mapping[str, Any]) -> bool:
    return payload["kind"] == "request" and any(
        part["part_kind"] == "user-prompt" for part in payload["parts"]
    )


def _step(part: Mapping[str, Any]) -> dict[str, str] | None:
    """The step a part of a subagent's response stands for, as the client reads it."""
    match part["part_kind"]:
        case "thinking":
            return {"kind": "reasoning"}
        case "text":
            return {"kind": "message"}
        case "tool-call":
            return {"kind": "tool", "tool_name": part["tool_name"]}
        case _:
            return None


def _transcript(part: dict[str, Any]) -> dict[str, Any]:
    """*part* with the transcript of the subagent history it stores in place of the history."""
    chunk = part.get("metadata")

    if not isinstance(chunk, dict) or chunk.get("type") != _HISTORY:
        return part

    steps = [
        step
        for messages in chunk["data"].values()
        for message in messages
        if message["kind"] == "response"
        for response_part in message["parts"]
        if (step := _step(response_part)) is not None
    ]

    return {
        **part,
        "metadata": {
            "type": "data-tool-output",
            "id": chunk.get("id"),
            "data": {"transcript": "subagent", "steps": steps},
            "transient": None,
        },
    }


def _instructions(nodes: Sequence[Node]) -> str | None:
    """The prompt of the first request, a root before any other, as creation times tie within a turn."""
    requests = [
        node.payload["instructions"]
        for node in sorted(nodes, key=lambda node: node.parent_id is not None)
        if node.payload["kind"] == "request"
        and node.payload.get("instructions") is not None
    ]

    return requests[0] if requests else None


def _pending(path: Sequence[Node]) -> dict[str, Any] | None:
    """The requests the last response of *path* stored, without the calls answered after it."""
    at = next(
        (
            i
            for i in reversed(range(len(path)))
            if path[i].payload["kind"] == "response"
        ),
        None,
    )

    if (
        at is None
        or (stored := (path[at].payload.get("metadata") or {}).get(_DEFERRED)) is None
    ):
        return None

    answered = {
        part["tool_call_id"]
        for node in path[at + 1 :]
        for part in node.payload["parts"]
        if part["part_kind"] == "tool-return"
    }
    calls = [c for c in stored["calls"] if c["tool_call_id"] not in answered]
    approvals = [c for c in stored["approvals"] if c["tool_call_id"] not in answered]

    return (
        {**stored, "calls": calls, "approvals": approvals}
        if calls or approvals
        else None
    )


def _check_rooted(by_id: Mapping[UUID, Node]) -> None:
    """Raise a ``ValueError`` unless every message's parents lead up to a root."""
    rooted: set[UUID] = set()

    for node in by_id.values():
        seen: set[UUID] = set()
        walk = node.id

        while walk is not None and walk not in rooted:
            if walk in seen:
                raise ValueError(f"message {walk} is its own ancestor")

            seen.add(walk)
            walk = by_id[walk].parent_id

        rooted |= seen


def record(nodes: Sequence[Node]) -> Record:
    """The store's record of the tree *nodes*, in the order they were created.

    The newest leaf is the head and the other leaves are the branches, oldest
    first.  Each message keeps the UI id the client knew it by: a prompt its
    own, and every other message that of the reply it opened or belongs to.
    A compaction stored ahead of a prompt showed at the end of the reply
    before it, so it keeps showing there.  The paused requests a response
    carried move to the record, as does the prompt of the first request, which
    the requests sent under it drop, and each subagent history becomes its
    transcript.

    Raises:
        ValueError: If the messages' parents form a cycle.
    """
    by_id = {node.id: node for node in nodes}
    parents = {node.parent_id for node in nodes}
    leaves = [node.id for node in nodes if node.id not in parents]
    ids = {node.id: uuid4() for node in nodes}
    instructions = _instructions(nodes)
    ui_ids: dict[UUID, str] = {}

    def opens(node: Node) -> bool:
        parent = None if node.parent_id is None else by_id[node.parent_id]

        return _is_prompt(node.payload) or parent is None or _is_prompt(parent.payload)

    _check_rooted(by_id)

    # Each message walks up to the message opening its UI message, once.
    for node in nodes:
        chain: list[Node] = []
        walk = node

        while walk.id not in ui_ids and not opens(walk):
            chain.append(walk)
            walk = by_id[walk.parent_id] if walk.parent_id is not None else walk

        top = ui_ids.setdefault(walk.id, str(walk.id))

        for member in chain:
            ui_ids[member.id] = top

    def path(leaf: UUID | None) -> list[Node]:
        chain: list[Node] = []

        while leaf is not None:
            chain.append(by_id[leaf])
            leaf = by_id[leaf].parent_id

        return chain[::-1]

    def payload(node: Node) -> dict[str, Any]:
        metadata = {
            k: v
            for k, v in (node.payload.get("metadata") or {}).items()
            if k != _DEFERRED
        }
        namespace = {
            **(metadata.get(_NAMESPACE) or {}),
            _UI_MESSAGE_ID: ui_ids[node.id],
        }

        stored = {
            k: v
            for k, v in node.payload.items()
            if k != "instructions" or v != instructions
        }

        return {
            **stored,
            "metadata": {**metadata, _NAMESPACE: namespace},
            "parts": [_transcript(part) for part in node.payload["parts"]],
        }

    head = leaves[-1] if leaves else None

    return Record(
        head_id=None if head is None else ids[head],
        branches=[ids[leaf] for leaf in leaves[:-1]],
        deferred_tool_requests=_pending(path(head)),
        instructions=instructions,
        entries=[
            Entry(
                old_id=node.id,
                id=ids[node.id],
                parent_id=None if node.parent_id is None else ids[node.parent_id],
                payload=payload(node),
            )
            for node in nodes
        ],
    )


def _rewrite() -> None:
    bind = op.get_bind()
    conversations = (
        bind.execute(sa.text("SELECT id FROM conversations")).scalars().all()
    )

    for conversation_id in conversations:
        rows = bind.execute(
            sa.text(
                "SELECT id, parent_id, payload FROM messages "
                "WHERE conversation_id = :c ORDER BY created_at, id"
            ),
            {"c": conversation_id},
        ).mappings()
        try:
            stored = record([Node(r["id"], r["parent_id"], r["payload"]) for r in rows])
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            error.add_note(f"in conversation {conversation_id}")
            raise

        if stored.entries:
            bind.execute(
                sa.text(
                    "UPDATE messages SET id = :id, parent_id = :parent_id, "
                    "payload = CAST(:payload AS jsonb) "
                    "WHERE conversation_id = :c AND id = :old_id"
                ),
                [
                    {
                        "id": entry.id,
                        "parent_id": entry.parent_id,
                        "payload": json.dumps(entry.payload),
                        "c": conversation_id,
                        "old_id": entry.old_id,
                    }
                    for entry in stored.entries
                ],
            )

        bind.execute(
            sa.text(
                "UPDATE conversations SET head_id = :head_id, "
                "branches = CAST(:branches AS jsonb), "
                "deferred_tool_requests = CAST(:deferred AS jsonb), "
                "instructions = :instructions, revision = :revision "
                "WHERE id = :c"
            ),
            {
                "head_id": stored.head_id,
                "branches": json.dumps([str(tip) for tip in stored.branches]),
                "deferred": None
                if stored.deferred_tool_requests is None
                else json.dumps(stored.deferred_tool_requests),
                "instructions": stored.instructions,
                "revision": str(uuid4()),
                "c": conversation_id,
            },
        )


def upgrade() -> None:
    op.add_column("conversations", sa.Column("revision", sa.String(), nullable=True))
    op.add_column("conversations", sa.Column("head_id", sa.Uuid(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("branches", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "conversations",
        sa.Column("usage", JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "conversations", sa.Column("deferred_tool_requests", JSONB(), nullable=True)
    )
    op.add_column("conversations", sa.Column("forked_from", sa.Uuid(), nullable=True))
    op.add_column("conversations", sa.Column("forked_at", sa.Uuid(), nullable=True))
    op.add_column(
        "conversations", sa.Column("instructions", sa.String(), nullable=True)
    )

    op.drop_constraint(
        op.f("fk_messages_conversation_id_parent_id_messages"),
        "messages",
        type_="foreignkey",
    )
    # While the old key and indexes still serve its lookups.
    _rewrite()

    op.drop_index(op.f("ix_messages_conversation_id_parent_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id_created_at"), table_name="messages")
    op.drop_constraint(op.f("pk_messages"), "messages", type_="primary")

    op.alter_column("conversations", "revision", nullable=False)
    op.create_primary_key(op.f("pk_messages"), "messages", ["id"])
    op.create_foreign_key(
        op.f("fk_messages_parent_id_messages"),
        "messages",
        "messages",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_messages_conversation_id"), "messages", ["conversation_id"]
    )
    op.create_index(op.f("ix_messages_parent_id"), "messages", ["parent_id"])


def downgrade() -> None:
    raise RuntimeError("The move to a conversation store is one-way")
