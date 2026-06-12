"""Conversation repository (replaces ``hivegent.messages``).

Each turn mirrors the agent's authoritative message list into SQL,
replacing the conversation's rows so the stored copy always matches what
the run produced — a turn that errors keeps its user prompt and partial
output instead of vanishing, and edit / regenerate / retry replace the
turn they rewrite instead of appending a duplicate.

The one thing the browser's echoed history drops is
``ToolReturnPart.metadata`` (the ``DataChunk`` tool-output payload the
frontend renders): the Vercel adapter ignores those data parts on the
way back.  Only the freshly generated tail of a turn still carries live
metadata, so :func:`_restore_tool_metadata` re-attaches it onto echoed
tool returns from the prior rows, keyed by ``tool_call_id``.
"""

import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, TypedDict

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows, new_id
from .engine import session
from .models import Conversation, Message, MessageKind
from .users import ensure_user

__all__ = [
    "ConversationData",
    "ConversationExport",
    "ConversationSummary",
    "conversation_exists",
    "create_compacted_conversation",
    "delete_all_conversations",
    "export_conversation",
    "extract_title",
    "list_conversations",
    "load_conversation",
    "load_conversation_summary",
    "load_messages",
    "remove_conversation",
    "replace_messages",
    "set_conversation_title",
]


# ─── Boundary types ────────────────────────────────────────────────────


class ConversationData(BaseModel):
    """Full conversation as exposed to the API and the agent runtime."""

    id: str = Field(description="Conversation identifier")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    messages: list[ModelMessage] = Field(default_factory=list)
    compacted_from: str | None = Field(default=None)

    @field_validator("messages", mode="before")
    @classmethod
    def _validate_messages(cls, v: Any) -> list[ModelMessage]:
        return ModelMessagesTypeAdapter.validate_python(v)

    @field_serializer("messages")
    @classmethod
    def _serialize_messages(cls, v: list[ModelMessage]) -> Any:
        return _dump_messages(v)


class ConversationSummary(BaseModel):
    """Lightweight conversation row for list views."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    compacted_from: str | None = None


class ExportMessage(BaseModel):
    """One stored turn, dumped verbatim from the database for export."""

    idx: int
    kind: MessageKind
    created_at: datetime
    payload: dict[str, Any]


class ConversationExport(BaseModel):
    """A whole conversation with its raw message payloads for debugging.

    Unlike :class:`ConversationData`, the payloads are passed through
    untouched (no ``ModelMessage`` round-trip), so the export mirrors
    exactly what is stored in the ``messages`` table.
    """

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    compacted_from: str | None = None
    messages: list[ExportMessage] = Field(default_factory=list)


# ─── Codecs ────────────────────────────────────────────────────────────


def _dump_messages(msgs: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    return ModelMessagesTypeAdapter.dump_python(list(msgs), mode="json")


def _load_messages(payloads: Sequence[dict[str, Any]]) -> list[ModelMessage]:
    """Round-trip messages from JSON, restoring ``DataChunk`` metadata.

    Pydantic AI's ``ToolReturnPart.metadata`` is typed ``Any`` and loses
    its concrete shape across JSON.  Without restoring it, downstream
    ``dump_messages`` silently drops the ``data-tool-output`` parts the
    frontend needs.
    """
    messages = ModelMessagesTypeAdapter.validate_python(payloads)
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart) or not isinstance(
                part.metadata, dict
            ):
                continue
            with contextlib.suppress(ValidationError, TypeError):
                part.metadata = DataChunk(**part.metadata)
    return messages


def _message_kind(msg: ModelMessage) -> MessageKind:
    if isinstance(msg, ModelRequest):
        return MessageKind.REQUEST
    if isinstance(msg, ModelResponse):
        return MessageKind.RESPONSE
    raise TypeError(f"Unknown ModelMessage subtype: {type(msg).__name__}")


def extract_title(messages: Sequence[ModelMessage]) -> str | None:
    """Pull a one-line title from the first user prompt, if any."""
    for msg in messages:
        for part in msg.parts:
            if not isinstance(part, UserPromptPart) or not isinstance(
                part.content, str
            ):
                continue
            text = part.content.strip()
            if not text:
                continue
            first_line = text.split("\n", 1)[0]
            return first_line if len(first_line) <= 100 else first_line[:97] + "..."
    return None


# ─── Summaries ─────────────────────────────────────────────────────────
#
# The sidebar's "N messages" should mirror what a reader sees: their own
# prompts and the assistant's text replies.  The tool-call / tool-return
# rows that sit between a question and its answer are an agent-loop
# implementation detail, so they are left out of the count.


def _visible_message_count() -> ColumnElement[int]:
    """Correlated count of a conversation's user prompts and reply texts.

    A user prompt only ever appears in a request and assistant text only
    in a response, so one part-kind test selects a visible row without
    consulting its ``kind``.
    """
    return (
        select(func.count())
        .where(
            Message.conversation_id == Conversation.id,
            or_(
                Message.payload.contains({"parts": [{"part_kind": "user-prompt"}]}),
                Message.payload.contains({"parts": [{"part_kind": "text"}]}),
            ),
        )
        .scalar_subquery()
    )


def _to_summary(conv: Conversation, message_count: int) -> ConversationSummary:
    """Build a list-view summary for *conv* with a precomputed count."""
    return ConversationSummary(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=message_count,
        compacted_from=conv.compacted_from_id,
    )


# ─── Reads ─────────────────────────────────────────────────────────────


async def _get_owned(
    s: AsyncSession, user_id: str, conversation_id: str
) -> Conversation | None:
    """Return the conversation if it exists and is owned by ``user_id``."""
    conv = await s.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        return None
    return conv


async def load_conversation(
    user_id: str, conversation_id: str
) -> ConversationData | None:
    """Return full conversation data, or ``None`` if missing or not owned."""
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
        if conv is None:
            return None
        rows = (
            (
                await s.execute(
                    select(Message.payload)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.idx)
                )
            )
            .scalars()
            .all()
        )
    return ConversationData(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=_load_messages(rows),
        compacted_from=conv.compacted_from_id,
    )


async def export_conversation(
    user_id: str, conversation_id: str
) -> ConversationExport | None:
    """Return a conversation with its raw message payloads, or ``None``.

    Intended for debugging exports: payloads are returned exactly as
    stored, without the ``ModelMessage`` validation round-trip.
    """
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
        if conv is None:
            return None
        rows = (
            await s.execute(
                select(
                    Message.idx,
                    Message.kind,
                    Message.created_at,
                    Message.payload,
                )
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.idx)
            )
        ).all()
    return ConversationExport(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        compacted_from=conv.compacted_from_id,
        messages=[
            ExportMessage(idx=idx, kind=kind, created_at=created_at, payload=payload)
            for idx, kind, created_at, payload in rows
        ],
    )


async def load_messages(user_id: str, conversation_id: str) -> list[ModelMessage]:
    """Return just the message list for a conversation."""
    async with session() as s:
        rows = (
            (
                await s.execute(
                    select(Message.payload)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.user_id == user_id,
                    )
                    .order_by(Message.idx)
                )
            )
            .scalars()
            .all()
        )
    return _load_messages(rows)


async def list_conversations(user_id: str) -> list[ConversationSummary]:
    """List a user's conversations newest first.

    Excludes empty conversations to mirror the previous file-walker
    behaviour (skipped JSONs without any messages).
    """
    count = _visible_message_count()
    async with session() as s:
        rows = (
            await s.execute(
                select(Conversation, count)
                .where(Conversation.user_id == user_id, count > 0)
                .order_by(Conversation.updated_at.desc())
            )
        ).all()
    return [_to_summary(conv, int(n)) for conv, n in rows]


async def load_conversation_summary(
    user_id: str, conversation_id: str
) -> ConversationSummary | None:
    """Return one conversation's summary, or ``None`` if missing or not owned."""
    async with session() as s:
        row = (
            await s.execute(
                select(Conversation, _visible_message_count()).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
        ).one_or_none()
    return None if row is None else _to_summary(row[0], int(row[1]))


async def conversation_exists(user_id: str, conversation_id: str) -> bool:
    """Return whether *conversation_id* exists and is owned by *user_id*."""
    async with session() as s:
        owner = await s.scalar(
            select(Conversation.user_id).where(Conversation.id == conversation_id)
        )
    return owner == user_id


# ─── Writes ────────────────────────────────────────────────────────────


class _MessageRow(TypedDict):
    """One row for the :func:`replace_messages` bulk upsert."""

    conversation_id: str
    idx: int
    kind: MessageKind
    payload: dict[str, Any]


def _has_stripped_tool_returns(messages: Sequence[ModelMessage]) -> bool:
    """Whether any tool return arrived without metadata (a browser echo).

    A turn with none — no tools, or only its freshly generated tail — has
    nothing to restore, so the prior rows need not be read at all.
    """
    return any(
        isinstance(part, ToolReturnPart) and part.metadata is None
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )


def _restore_tool_metadata(
    incoming: Sequence[ModelMessage], existing: Sequence[ModelMessage]
) -> None:
    """Re-attach tool-output metadata the browser's echoed history dropped.

    The Vercel adapter discards ``ToolReturnPart.metadata`` (the
    ``DataChunk`` the frontend renders) when it loads client messages, so
    every tool return except this turn's freshly generated tail comes back
    with ``metadata=None``.  Restore it in place from the prior rows, keyed
    by ``tool_call_id`` (unique within a conversation); the live metadata on
    the new tail has no match yet and is left untouched.
    """
    saved = {
        part.tool_call_id: part.metadata
        for msg in existing
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.metadata is not None
    }
    if not saved:
        return
    for msg in incoming:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.metadata is None
                and (metadata := saved.get(part.tool_call_id)) is not None
            ):
                part.metadata = metadata


async def replace_messages(
    user_id: str,
    conversation_id: str,
    messages: Sequence[ModelMessage],
) -> None:
    """Mirror a turn's full message list into *conversation_id*.

    *messages* is the whole conversation as the run sees it (its
    ``all_messages()`` on a clean finish, or the partial transcript captured
    so far when the run was interrupted), which replaces every stored row.
    Mirroring rather than appending keeps the stored copy equal to the live
    state through errors, edits, regenerations, and retries — all of which
    rewrite the tail of the conversation client-side.

    The rows are upserted by ``idx`` rather than dropped and re-inserted, so a
    message that keeps its position keeps its original ``created_at`` instead
    of being restamped every turn (``ON CONFLICT`` rewrites only ``kind`` and
    ``payload``); only the rewritten tail (rows past the new length) is deleted.

    The row is created lazily on the first turn — the id-less ``/chat``
    endpoint mints a server ID and only here does it become a real record,
    so abandoned chats never leave an empty row behind.  Existing-conversation
    chats are guarded at the route boundary, so an unknown ID only reaches
    this path as a fresh mint.
    """
    msg_list = list(messages)
    if not msg_list:
        return

    async with session() as s:
        # Serialise concurrent writes to the same conversation: the ownership
        # check, metadata read, and idx-keyed upsert below form a
        # read-then-write sequence that two racing turns (duplicate submit,
        # multiple tabs) would otherwise interleave.  The transaction-scoped
        # advisory lock auto-releases on commit/rollback.
        await s.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtextextended(conversation_id, 0))
            )
        )
        await ensure_user(s, user_id)

        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            conv = Conversation(id=conversation_id, user_id=user_id)
            s.add(conv)
        elif conv.user_id != user_id:
            raise PermissionError(
                f"conversation {conversation_id} is not owned by {user_id}"
            )
        elif _has_stripped_tool_returns(msg_list):
            # The upsert needs no prior column, so read just the payloads, and
            # only when there is dropped tool metadata to restore.
            stored = await s.scalars(
                select(Message.payload)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.idx)
            )
            _restore_tool_metadata(msg_list, _load_messages(stored.all()))

        if conv.title is None:
            conv.title = extract_title(msg_list)

        rows: list[_MessageRow] = [
            {
                "conversation_id": conversation_id,
                "idx": idx,
                "kind": _message_kind(msg),
                "payload": payload,
            }
            for idx, (msg, payload) in enumerate(
                zip(msg_list, _dump_messages(msg_list), strict=True)
            )
        ]
        insert = pg_insert(Message).values(rows)
        await s.execute(
            insert.on_conflict_do_update(
                index_elements=[Message.conversation_id, Message.idx],
                set_={"kind": insert.excluded.kind, "payload": insert.excluded.payload},
            )
        )
        # Trim a tail a shorter turn (e.g. an edit that replays fewer messages)
        # left behind; a no-op when the conversation grew or kept its length.
        await s.execute(
            delete(Message).where(
                Message.conversation_id == conversation_id,
                Message.idx >= len(rows),
            )
        )

        # Touch the row so `onupdate=_now` bumps `updated_at` even when only
        # child Message rows change.
        conv.updated_at = datetime.now(UTC)


async def set_conversation_title(
    user_id: str, conversation_id: str, title: str
) -> ConversationSummary | None:
    """Set the title on a conversation owned by *user_id*.

    Returns the updated summary, or ``None`` if no such conversation
    exists for that user.
    """
    async with session() as s:
        result = await s.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
            .values(title=title)
        )
        if affected_rows(result) == 0:
            return None
    return await load_conversation_summary(user_id, conversation_id)


async def remove_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete a conversation (cascades to its messages)."""
    async with session() as s:
        result = await s.execute(
            delete(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
    return affected_rows(result) > 0


async def delete_all_conversations(user_id: str) -> int:
    """Delete every conversation owned by *user_id*.  Returns the deleted count."""
    async with session() as s:
        result = await s.execute(
            delete(Conversation).where(Conversation.user_id == user_id)
        )
    return affected_rows(result)


async def create_compacted_conversation(
    user_id: str,
    original_conversation_id: str | None,
    summary_message: ModelMessage,
    title: str,
) -> str:
    """Persist a fresh conversation seeded with one summary message.

    ``original_conversation_id`` is ``None`` when the source conversation
    was never persisted (e.g. a draft compacted before its first turn
    committed), in which case there is no ``compacted_from`` link to set.
    """
    conversation_id = new_id()
    async with session() as s:
        await ensure_user(s, user_id)
        s.add(
            Conversation(
                id=conversation_id,
                user_id=user_id,
                title=title,
                compacted_from_id=original_conversation_id,
            )
        )
        s.add(
            Message(
                conversation_id=conversation_id,
                idx=0,
                kind=_message_kind(summary_message),
                payload=_dump_messages([summary_message])[0],
            )
        )
    return conversation_id
