"""Conversations, kept by a pydantic-ai ``ConversationStore`` on PostgreSQL.

:class:`PostgresConversationStore` implements the store's storage primitives
on the ``conversations`` (records) and ``messages`` (entries) tables, so
pydantic-ai decides what a save appends, where a rewritten history branches,
and which calls a paused run awaits.  A turn loads its conversation, runs on
it, and saves it (:mod:`hivegent.server.routes.conversations`).

pydantic-ai stamps the whole system prompt on every request it sends, so the
conversation keeps the prompt its first request was sent under once, and a
request sent under it is stored without it and read back with it.
"""

import contextlib
from abc import ABC
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, override
from uuid import UUID

from cachetools import LRUCache
from pydantic import TypeAdapter
from pydantic_ai import AgentRunResult, ModelMessagesTypeAdapter
from pydantic_ai.conversation import Conversation as RunConversation
from pydantic_ai.conversation_store import (
    ConversationConflict,
    ConversationDetails,
    ConversationEntry,
    ConversationRecord,
    ConversationStore,
)
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai.usage import RunUsage
from pydantic_core import from_json, to_json
from sqlalchemy import delete, event, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased

from ...config import parse_uuid
from ...jobs import ConversationsChanged, manager
from ..engine import session
from ..models import Conversation, Message
from ..users import ensure_user

__all__ = [
    "OwnedConversationStore",
    "PostgresConversationStore",
    "store",
]

_NO_OWNER = "A conversation is stored with its owner as its scope."

_TITLE_ONLY = "A conversation stores a title beside its record, no metadata."

_USAGE = TypeAdapter(RunUsage)

_DEFERRED = TypeAdapter(DeferredToolRequests)


def _dump_message(message: ModelMessage, prompt: str | None = None) -> dict[str, Any]:
    """The JSON of *message* as it is stored, a request sent under the conversation's *prompt* without it."""
    payload = ModelMessagesTypeAdapter.dump_python([message], mode="json")[0]

    if prompt is not None and payload.get("instructions") == prompt:
        del payload["instructions"]

    return payload


def _load_message(payload: dict[str, Any], prompt: str | None = None) -> ModelMessage:
    """A message from its stored JSON (:func:`_restored`), a request stored without instructions sent under *prompt*."""
    if payload["kind"] == "request" and "instructions" not in payload:
        payload = {**payload, "instructions": prompt}

    return _restored(ModelMessagesTypeAdapter.validate_python([payload])[0])


def _restored(message: ModelMessage) -> ModelMessage:
    """*message* decoded from JSON, its ``DataChunk`` tool output restored.

    Pydantic AI's ``ToolReturnPart.metadata`` is typed ``Any`` and loses its
    concrete shape across JSON.  Without restoring it, ``dump_messages``
    silently drops the ``data-tool-output`` parts the frontend needs.
    """
    if isinstance(message, ModelRequest):
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.metadata, dict):
                with contextlib.suppress(ValueError):
                    part.metadata = DataChunk.model_validate(part.metadata)

    return message


def _announce(s: AsyncSession, owner: str) -> None:
    """Tell every client of *owner* to list its conversations again once *s* commits, never on a rollback."""

    def publish(_session: Session) -> None:
        manager.publish(owner, ConversationsChanged())

    event.listen(s.sync_session, "after_commit", publish, once=True)


def _still_pending(
    pending: DeferredToolRequests | None, messages: Iterable[ModelMessage]
) -> DeferredToolRequests | None:
    """*pending* unless *messages* already return a result for any of its calls.

    A stop that lands after a run paused closes the calls it awaited, and a
    call can only ever be closed with all of its run's.
    """
    if pending is None:
        return None

    answered = {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    awaited = {call.tool_call_id for call in (*pending.calls, *pending.approvals)}

    return None if answered & awaited else pending


def _uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)


def _uuid_text(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _record(row: Conversation) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=str(row.id),
        head_id=_uuid_text(row.head_id),
        usage=_USAGE.validate_python(row.usage) if row.usage else RunUsage(),
        deferred_tool_requests=None
        if row.deferred_tool_requests is None
        else _DEFERRED.validate_python(row.deferred_tool_requests),
        scope=row.user_id,
        title=row.title,
        forked_from=_uuid_text(row.forked_from),
        forked_at=_uuid_text(row.forked_at),
        branches=list(row.branches),
        revision=row.revision,
        updated_at=row.updated_at,
    )


def _entry(row: Message, prompt: str | None) -> ConversationEntry:
    return ConversationEntry(
        id=str(row.id),
        parent_id=_uuid_text(row.parent_id),
        message=_load_message(row.payload, prompt),
    )


@dataclass(frozen=True, slots=True)
class _Cached:
    """A stored message as the cache keeps it, its JSON as stored."""

    conversation_id: UUID
    parent_id: str | None
    payload: bytes


type _Stored = tuple[str, str | None, dict[str, Any]]
"""A stored message: its id, its parent's id, and its JSON as stored."""


def _owner(scope: str | None) -> str:
    """*scope*, the owner every read or change of a conversation names.

    Raises:
        UserError: For no scope, which pydantic-ai reads as every owner's.
    """
    if scope is None:
        raise UserError(_NO_OWNER)

    return scope


class OwnedConversationStore(ConversationStore, ABC):
    """A ``ConversationStore`` whose every read, list, and save names the owner as its scope.

    pydantic-ai reads a missing ``scope`` as every owner's, so this refuses
    one, and only :meth:`owner_of`, the ownership check's read, names none.
    """

    @override
    async def save(
        self,
        conversation: RunConversation | AgentRunResult[Any],
        *,
        scope: str | None = None,
    ) -> list[ConversationEntry]:
        return await super().save(conversation, scope=_owner(scope))

    @override
    async def list_conversations(
        self, *, scope: str | None = None, limit: int | None = None
    ) -> list[ConversationRecord]:
        return await super().list_conversations(scope=_owner(scope), limit=limit)

    @override
    async def _read_record(
        self, conversation_id: str, *, scope: str | None
    ) -> ConversationRecord | None:
        """The record of *conversation_id* if *scope* owns it, the read every scoped call goes through."""
        return await super()._read_record(conversation_id, scope=_owner(scope))

    async def owner_of(self, conversation_id: str) -> str | None:
        """The owner of *conversation_id*, ``None`` while no record holds it."""
        record = await self.read_record(conversation_id)

        return None if record is None else record.scope

    async def owners_of(self, conversation_ids: Collection[UUID]) -> dict[UUID, str]:
        """The owner of each of *conversation_ids* a record holds."""
        owners = {key: await self.owner_of(str(key)) for key in conversation_ids}

        return {key: owner for key, owner in owners.items() if owner is not None}


@dataclass(slots=True)
class PostgresConversationStore(OwnedConversationStore):
    """A ``ConversationStore`` on the ``conversations`` and ``messages`` tables.

    A record is a ``conversations`` row and an entry a ``messages`` row, which
    also names the conversation that stored it, so deleting a conversation
    deletes its messages with it: nothing forks a conversation, so no other
    record reaches them.  A record must name its owner as its scope, and so
    must every read or change (:class:`OwnedConversationStore`).  Saving a
    record keeps the title it was claimed with or set to since
    (:meth:`write_details`).

    The messages it reads or writes stay cached as stored, up to
    *cache_bytes* of the least recently used, and the system prompts of their
    conversations up to *prompt_cache_bytes*, so a history read again needs
    no query.  A stored message never changes and its id is never reused, and
    a conversation's prompt is set once by the save that first carries one,
    so neither is ever stale.  A deleted conversation's messages may linger
    there until dropped, reachable only through a record naming them, which
    went with it.
    """

    cache_bytes: int = 64 * 2**20
    prompt_cache_bytes: int = 8 * 2**20
    _cache: LRUCache[str, _Cached] = field(init=False)
    _prompts: LRUCache[UUID, str | None] = field(init=False)

    def __post_init__(self) -> None:
        self._cache = LRUCache(
            self.cache_bytes, getsizeof=lambda cached: len(cached.payload)
        )
        self._prompts = LRUCache(
            self.prompt_cache_bytes, getsizeof=lambda prompt: 1 + len(prompt or "")
        )

    def _keep(
        self, conversation_id: UUID, prompt: str | None, stored: Iterable[_Stored]
    ) -> None:
        """Cache the *stored* messages of *conversation_id* and its *prompt*, except one exceeding its budget alone."""
        with contextlib.suppress(ValueError):
            self._prompts[conversation_id] = prompt

        for entry_id, parent_id, payload in stored:
            with contextlib.suppress(ValueError):
                self._cache[entry_id] = _Cached(
                    conversation_id, parent_id, to_json(payload)
                )

    def _cached_history(
        self, head_id: str, limit: int | None
    ) -> list[ConversationEntry] | None:
        """:meth:`read_history` from the cache, ``None`` unless it holds every entry and their prompt.

        Each entry is decoded afresh, so a caller may change its message.
        """
        path: list[tuple[str, _Cached]] = []
        entry_id: str | None = head_id

        while entry_id is not None and (limit is None or len(path) < limit):
            if (cached := self._cache.get(entry_id)) is None:
                return None

            path.append((entry_id, cached))
            entry_id = cached.parent_id

        try:
            prompt = self._prompts[path[0][1].conversation_id]
        except KeyError:
            return None

        return [
            ConversationEntry(
                id=entry_id,
                parent_id=cached.parent_id,
                message=_load_message(from_json(cached.payload), prompt),
            )
            for entry_id, cached in reversed(path)
        ]

    @override
    async def save(
        self,
        conversation: RunConversation | AgentRunResult[Any],
        *,
        scope: str | None = None,
    ) -> list[ConversationEntry]:
        """Save *conversation*, never awaiting a call its history already closed."""
        held = (
            conversation.conversation
            if isinstance(conversation, AgentRunResult)
            else conversation
        )
        held.deferred_tool_requests = _still_pending(
            held.deferred_tool_requests, held.messages
        )

        return await super().save(conversation, scope=scope)

    @override
    async def read_record(self, conversation_id: str) -> ConversationRecord | None:
        if (key := parse_uuid(conversation_id)) is None:
            return None

        async with session() as s:
            row = await s.get(Conversation, key)

            return None if row is None else _record(row)

    @override
    async def owner_of(self, conversation_id: str) -> str | None:
        if (key := parse_uuid(conversation_id)) is None:
            return None

        async with session() as s:
            return await s.scalar(
                select(Conversation.user_id).where(Conversation.id == key)
            )

    @override
    async def owners_of(self, conversation_ids: Collection[UUID]) -> dict[UUID, str]:
        query = select(Conversation.id, Conversation.user_id).where(
            Conversation.id.in_(conversation_ids)
        )

        async with session() as s:
            return dict((await s.execute(query)).tuples().all())

    @override
    async def read_records(
        self, *, scope: str | None = None, limit: int | None = None
    ) -> Sequence[ConversationRecord]:
        query = (
            select(Conversation)
            .where(Conversation.user_id == _owner(scope))
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )

        async with session() as s:
            return [_record(row) for row in (await s.scalars(query)).all()]

    @override
    async def write_record(
        self,
        record: ConversationRecord,
        *,
        new_entries: Sequence[ConversationEntry] = (),
        expected_revision: str | None,
    ) -> None:
        if record.scope is None:
            raise UserError(_NO_OWNER)

        key = UUID(record.conversation_id)
        # The prompt of the first request the conversation stores, kept once.
        first = next(
            (
                message.instructions
                for entry in new_entries
                if isinstance(message := entry.message, ModelRequest)
                and message.instructions is not None
            ),
            None,
        )
        values = {
            "user_id": record.scope,
            "revision": record.revision,
            "head_id": _uuid(record.head_id),
            "branches": list(record.branches),
            "usage": _USAGE.dump_python(record.usage, mode="json"),
            "deferred_tool_requests": None
            if record.deferred_tool_requests is None
            else _DEFERRED.dump_python(record.deferred_tool_requests, mode="json"),
            "forked_from": _uuid(record.forked_from),
            "forked_at": _uuid(record.forked_at),
            "updated_at": record.updated_at,
        }
        new_ids = {entry.id for entry in new_entries}

        async with session() as s:
            if expected_revision is None:
                await ensure_user(s, record.scope)
                written = (
                    await s.execute(
                        pg_insert(Conversation)
                        .values(
                            id=key, title=record.title, instructions=first, **values
                        )
                        .on_conflict_do_nothing()
                        .returning(Conversation.instructions)
                    )
                ).one_or_none()
            else:
                written = (
                    await s.execute(
                        update(Conversation)
                        .where(
                            Conversation.id == key,
                            Conversation.revision == expected_revision,
                        )
                        .values(
                            instructions=func.coalesce(
                                Conversation.instructions, first
                            ),
                            **values,
                        )
                        .returning(Conversation.instructions)
                    )
                ).one_or_none()

            if written is None:
                raise ConversationConflict(record.conversation_id)

            prompt = written.instructions
            stored = [
                (entry.id, entry.parent_id, _dump_message(entry.message, prompt))
                for entry in new_entries
            ]

            for entry_id, parent_id, payload in stored:
                s.add(
                    Message(
                        id=UUID(entry_id),
                        conversation_id=key,
                        parent_id=_uuid(parent_id),
                        payload=payload,
                    )
                )

            if (
                record.head_id is not None
                and record.head_id not in new_ids
                and await s.get(Message, UUID(record.head_id)) is None
            ):
                raise ConversationConflict(record.conversation_id)

            _announce(s, record.scope)

        self._keep(key, prompt, stored)

    @override
    async def write_details(
        self,
        conversation_id: str,
        details: ConversationDetails,
        *,
        expected_revision: str,
        expected: ConversationDetails | None = None,
    ) -> ConversationRecord:
        """Set the title, checked against *expected* and written in one ``UPDATE``.

        Raises:
            NotImplementedError: For metadata, which a conversation does not store.
        """
        expected = expected or {}

        if details.keys() != {"title"} or expected.keys() - {"title"}:
            raise NotImplementedError(_TITLE_ONLY)

        query = update(Conversation).where(
            Conversation.id == UUID(conversation_id),
            Conversation.revision == expected_revision,
        )

        if "title" in expected:
            query = query.where(
                Conversation.title.is_not_distinct_from(expected.get("title"))
            )

        async with session() as s:
            row = await s.scalar(
                query.values(title=details.get("title")).returning(Conversation)
            )

            if row is None:
                raise ConversationConflict(conversation_id)

            _announce(s, row.user_id)

            return _record(row)

    @override
    async def delete_record(
        self, conversation_id: str, *, expected_revision: str
    ) -> None:
        async with session() as s:
            owner = await s.scalar(
                delete(Conversation)
                .where(
                    Conversation.id == UUID(conversation_id),
                    Conversation.revision == expected_revision,
                )
                .returning(Conversation.user_id)
            )

            if owner is None:
                raise ConversationConflict(conversation_id)

            _announce(s, owner)

    @override
    async def read_entry(self, entry_id: str) -> ConversationEntry:
        async with session() as s:
            row = (
                await s.execute(
                    select(Message, Conversation.instructions)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(Message.id == UUID(entry_id))
                )
            ).one_or_none()

        if row is None:
            raise LookupError(entry_id)

        message, prompt = row.tuple()

        return _entry(message, prompt)

    @override
    async def read_history(
        self, head_id: str | None, *, limit: int | None = None
    ) -> list[ConversationEntry]:
        """The entries up to *head_id*, cached or walked up their parents in one recursive query."""
        if head_id is None or limit == 0:
            return []

        if (cached := self._cached_history(head_id, limit)) is not None:
            return cached

        anchor = (
            select(Message.id, Message.parent_id, literal(0).label("depth"))
            .where(Message.id == UUID(head_id))
            .cte(recursive=True)
        )
        parent = aliased(Message)
        step = select(parent.id, parent.parent_id, anchor.c.depth + 1).where(
            parent.id == anchor.c.parent_id
        )
        path = anchor.union_all(
            step if limit is None else step.where(anchor.c.depth + 1 < limit)
        )

        async with session() as s:
            rows = (
                await s.scalars(
                    select(Message)
                    .join(path, path.c.id == Message.id)
                    .order_by(path.c.depth.desc())
                )
            ).all()

            if not rows or str(rows[-1].id) != head_id:
                raise LookupError(head_id)

            conversation_id = rows[0].conversation_id
            prompt = await s.scalar(
                select(Conversation.instructions).where(
                    Conversation.id == conversation_id
                )
            )

        self._keep(
            conversation_id,
            prompt,
            ((str(row.id), _uuid_text(row.parent_id), row.payload) for row in rows),
        )

        return [_entry(row, prompt) for row in rows]


store = PostgresConversationStore()
"""The conversations of this app."""
