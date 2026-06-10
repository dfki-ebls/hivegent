"""Conversation repository (replaces ``hivegent.messages``).

Each turn appends new ``Message`` rows rather than rewriting a full
JSON file.  The metadata-preservation dance in the old module is no
longer needed: prior rows in SQL keep their original ``ToolReturnPart``
payloads, so the SDK losing metadata in the next request matters only
for what it sends us, not for what we have stored.
"""

import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

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
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows, new_id
from .engine import session
from .models import Conversation, Message, MessageKind
from .users import ensure_user

__all__ = [
    "ConversationData",
    "ConversationExport",
    "ConversationSummary",
    "append_messages",
    "conversation_exists",
    "create_compacted_conversation",
    "delete_all_conversations",
    "export_conversation",
    "extract_title",
    "list_conversations",
    "load_conversation",
    "load_messages",
    "messages_to_persist",
    "remove_conversation",
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
    msg_counts = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.count().label("n"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    async with session() as s:
        rows = (
            await s.execute(
                select(Conversation, msg_counts.c.n)
                .join(msg_counts, msg_counts.c.conversation_id == Conversation.id)
                .where(Conversation.user_id == user_id)
                .where(msg_counts.c.n > 0)
                .order_by(Conversation.updated_at.desc())
            )
        ).all()
    return [
        ConversationSummary(
            id=conv.id,
            title=conv.title or "",
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=int(count),
            compacted_from=conv.compacted_from_id,
        )
        for conv, count in rows
    ]


async def conversation_exists(user_id: str, conversation_id: str) -> bool:
    """Return whether *conversation_id* exists and is owned by *user_id*."""
    async with session() as s:
        owner = await s.scalar(
            select(Conversation.user_id).where(Conversation.id == conversation_id)
        )
    return owner == user_id


# ─── Writes ────────────────────────────────────────────────────────────


def _opens_a_turn(message: ModelMessage) -> bool:
    """Whether *message* is a request carrying a fresh user prompt."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def messages_to_persist(
    all_messages: Sequence[ModelMessage],
    new_messages: Sequence[ModelMessage],
) -> list[ModelMessage]:
    """Select the messages a finished turn should append to its conversation.

    Pass a run result's ``all_messages()`` and ``new_messages()``.  Whatever
    the agent generated this run (``new_messages()``) is always new.  The
    Vercel adapter folds the submitted message into ``message_history``
    instead of passing it as a separate prompt, so a fresh question appears
    as the ``UserPromptPart`` request sitting just before the generated tail
    and is stored with it.  A tool-approval resume has no new prompt in that
    slot — it holds the already-stored assistant turn being continued — so
    only the generated messages are appended.

    The stored row count cannot locate this boundary: the SDK re-segments
    the history it echoes back each turn, so its message count never lines
    up positionally with what we persisted, and diffing by count re-appends
    the previous turn's tail (the duplicate-message bug this avoids).
    """
    messages = list(all_messages)
    start = len(messages) - len(new_messages)
    if start > 0 and _opens_a_turn(messages[start - 1]):
        start -= 1
    return messages[start:]


async def append_messages(
    user_id: str,
    conversation_id: str,
    messages: Sequence[ModelMessage],
) -> None:
    """Append one turn's messages to *conversation_id*.

    *messages* must be just that turn's new messages (see
    :func:`messages_to_persist`), which are appended verbatim past the
    existing rows — prior payloads, including their tool-return metadata,
    are never re-read or overwritten.

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
        # Serialise concurrent appends to the same conversation: the
        # existence check, message-count read, and idx-assigned inserts
        # below form a read-then-write sequence that two racing turns
        # (duplicate submit, multiple tabs) would otherwise interleave —
        # tripping the conversations PK or the (conversation_id, idx) PK
        # and rolling back the turn.  The transaction-scoped advisory
        # lock auto-releases on commit/rollback.
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
            existing_count = 0
        elif conv.user_id != user_id:
            raise PermissionError(
                f"conversation {conversation_id} is not owned by {user_id}"
            )
        else:
            existing_count = int(
                await s.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == conversation_id)
                )
                or 0
            )

        if conv.title is None:
            conv.title = extract_title(msg_list)

        for offset, (msg, payload) in enumerate(
            zip(msg_list, _dump_messages(msg_list), strict=True)
        ):
            s.add(
                Message(
                    conversation_id=conversation_id,
                    idx=existing_count + offset,
                    kind=_message_kind(msg),
                    payload=payload,
                )
            )

        # Touch the row so `onupdate=_now` bumps `updated_at` even when
        # only child Message rows are inserted.
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
        row = (
            await s.execute(
                select(
                    Conversation,
                    select(func.count())
                    .where(Message.conversation_id == Conversation.id)
                    .scalar_subquery(),
                ).where(Conversation.id == conversation_id)
            )
        ).one()
    conv, message_count = row
    return ConversationSummary(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=int(message_count),
        compacted_from=conv.compacted_from_id,
    )


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
