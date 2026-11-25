"""Conversations, kept by a pydantic-ai ``ConversationStore`` on PostgreSQL.

:class:`PostgresConversationStore` implements the store's storage primitives
on the ``conversations`` (records) and ``messages`` (entries) tables, so
pydantic-ai decides what a save appends, where a rewritten history branches,
and which calls a paused run awaits.  A turn loads its conversation, runs on
it, and saves it (:mod:`hivegent.server.routes.conversations`).

A request keeps its instructions in a column, only where they change
(:class:`~..models.Message`), and a message travels as JSON text, which
pydantic parses straight into the message.
"""

import contextlib
import sys
from abc import ABC
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, override
from uuid import UUID

from cachetools import LRUCache
from pydantic import AfterValidator, ConfigDict, TypeAdapter
from pydantic_ai import AgentRunResult
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
from sqlalchemy import (
    Nullable,
    Select,
    and_,
    delete,
    event,
    literal,
    or_,
    select,
    update,
)
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


# The config of `pydantic_ai.ModelMessagesTypeAdapter`, for one message rather than a list.
_MESSAGE = TypeAdapter[ModelMessage](
    Annotated[ModelMessage, AfterValidator(_restored)],
    config=ConfigDict(
        defer_build=True, ser_json_bytes="base64", val_json_bytes="base64"
    ),
)


def _changes(message: ModelMessage, inherited: str | None) -> str | None:
    """The instructions *message* stores, where it changes the *inherited* ones, ``''`` for none."""
    if not isinstance(message, ModelRequest) or message.instructions == inherited:
        return None

    return sys.intern(message.instructions or "")


def _in_effect(changes: str | None, inherited: str | None) -> str | None:
    """The instructions in effect at a message storing *changes* below *inherited*."""
    return inherited if changes is None else changes or None


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


def _record(row: Conversation) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=str(row.id),
        head_id=row.head_id,
        usage=row.usage,
        deferred_tool_requests=row.deferred_tool_requests,
        scope=row.user_id,
        title=row.title,
        forked_from=row.forked_from,
        forked_at=row.forked_at,
        branches=list(row.branches),
        revision=row.revision,
        updated_at=row.updated_at,
    )


@dataclass(frozen=True, slots=True)
class _Stored:
    """A stored message, with the instructions it changes and the ones in effect at it."""

    id: str
    parent_id: str | None
    payload: str
    changes: str | None
    instructions: str | None

    def entry(self) -> ConversationEntry:
        message = _MESSAGE.validate_json(self.payload)

        if isinstance(message, ModelRequest):
            message.instructions = self.instructions

        return ConversationEntry(id=self.id, parent_id=self.parent_id, message=message)

    def row(self, conversation_id: UUID) -> Message:
        return Message(
            id=self.id,
            conversation_id=conversation_id,
            parent_id=self.parent_id,
            payload=self.payload,
            instructions=self.changes,
        )


def _stored_entries(
    entries: Iterable[ConversationEntry], in_effect: Mapping[str, str | None]
) -> list[_Stored]:
    """*entries* as stored, the instructions in effect at a parent outside them read from *in_effect*."""
    in_effect = dict(in_effect)
    stored: list[_Stored] = []

    for entry in entries:
        inherited = None if entry.parent_id is None else in_effect[entry.parent_id]
        changes = _changes(entry.message, inherited)
        in_effect[entry.id] = _in_effect(changes, inherited)
        payload = _MESSAGE.dump_json(entry.message, exclude={"instructions"}).decode()
        stored.append(
            _Stored(entry.id, entry.parent_id, payload, changes, in_effect[entry.id])
        )

    return stored


type _PathRow = tuple[str, str | None, str | None, str | None, int]
"""A message on a path: its id, its parent's id, the instructions it changes, its JSON as text unless above the window, and its depth."""


def _path_query(head_id: str, limit: int | None) -> Select[*_PathRow]:
    """The messages up to *head_id*, or only the last *limit* of them, in one recursive query.

    Above the last *limit*, the walk goes on to the nearest request changing
    the instructions, which are in effect at the first of them, without its JSON.
    """
    anchor = (
        select(
            Message.id,
            Message.parent_id,
            Message.instructions,
            literal(0).label("depth"),
        )
        .where(Message.id == head_id)
        .cte(recursive=True)
    )
    parent = aliased(Message)
    step = select(
        parent.id, parent.parent_id, parent.instructions, anchor.c.depth + 1
    ).where(parent.id == anchor.c.parent_id)

    if limit is None:
        path = anchor.union_all(step)
        joined = Message.id == path.c.id
    else:
        path = anchor.union_all(
            step.where(or_(anchor.c.depth + 1 < limit, anchor.c.instructions.is_(None)))
        )
        joined = and_(Message.id == path.c.id, path.c.depth < limit)

    return select(
        path.c.id,
        path.c.parent_id,
        path.c.instructions,
        Nullable(Message.payload),
        path.c.depth,
    ).outerjoin(Message, joined)


def _fold(path: Iterable[_PathRow]) -> list[_Stored]:
    """The messages of *path* within its window, root first, with the instructions in effect at each."""
    stored: list[_Stored] = []
    inherited: str | None = None

    for entry_id, parent_id, stored_changes, payload, _ in sorted(
        path, key=lambda row: row[4], reverse=True
    ):
        # Interned, so the cache keeps one string per prompt.
        changes = None if stored_changes is None else sys.intern(stored_changes)
        inherited = _in_effect(changes, inherited)

        if payload is not None:
            stored.append(_Stored(entry_id, parent_id, payload, changes, inherited))

    return stored


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

    The messages it reads or writes stay cached as stored, with the
    instructions in effect at each, up to *cache_bytes* of the least recently
    used, so a history read again needs no query.  A stored message never
    changes and its id is never reused, so it is never stale.  A deleted
    conversation's messages may linger there until dropped, reachable only
    through a record naming them, which went with it.
    """

    cache_bytes: int = 64 * 2**20
    _cache: LRUCache[str, _Stored] = field(init=False)

    def __post_init__(self) -> None:
        self._cache = LRUCache(
            self.cache_bytes,
            getsizeof=lambda stored: len(stored.payload) + len(stored.changes or ""),
        )

    def _keep(self, stored: Iterable[_Stored]) -> None:
        """Cache the *stored* messages, except one exceeding the budget alone."""
        for message in stored:
            with contextlib.suppress(ValueError):
                self._cache[message.id] = message

    def _cached_path(
        self, head_id: str, limit: int | None
    ) -> tuple[list[_Stored], str | None]:
        """The cached messages up to *head_id*, newest first, or the last *limit* of them, and the id of the first one it misses."""
        path: list[_Stored] = []
        entry_id: str | None = head_id

        while entry_id is not None and (limit is None or len(path) < limit):
            if (cached := self._cache.get(entry_id)) is None:
                return path, entry_id

            path.append(cached)
            entry_id = cached.parent_id

        return path, None

    async def _read_path(self, head_id: str, limit: int | None) -> list[_Stored]:
        """The stored messages up to *head_id*, root first, or only the last *limit* of them.

        Raises:
            LookupError: If no message is stored as *head_id*.
        """
        async with session() as s:
            stored = _fold((await s.execute(_path_query(head_id, limit))).tuples())

        if not stored or stored[-1].id != head_id:
            raise LookupError(head_id)

        self._keep(stored)

        return stored

    async def _instructions_at(self, entry_id: str) -> str | None:
        """The instructions in effect at *entry_id*, which its children inherit.

        Raises:
            LookupError: If no message is stored as *entry_id*.
        """
        if (cached := self._cache.get(entry_id)) is not None:
            return cached.instructions

        return (await self._read_path(entry_id, 1))[-1].instructions

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
        new_ids = {entry.id for entry in new_entries}

        try:
            stored = _stored_entries(
                new_entries,
                {
                    parent_id: await self._instructions_at(parent_id)
                    for entry in new_entries
                    if (parent_id := entry.parent_id) is not None
                    and parent_id not in new_ids
                },
            )
        except LookupError as error:
            raise ConversationConflict(record.conversation_id) from error

        values = {
            "user_id": record.scope,
            "revision": record.revision,
            "head_id": record.head_id,
            "branches": list(record.branches),
            "usage": record.usage,
            "deferred_tool_requests": record.deferred_tool_requests,
            "forked_from": record.forked_from,
            "forked_at": record.forked_at,
            "updated_at": record.updated_at,
        }

        async with session() as s:
            if expected_revision is None:
                await ensure_user(s, record.scope)
                written = await s.scalar(
                    pg_insert(Conversation)
                    .values(id=key, title=record.title, **values)
                    .on_conflict_do_nothing()
                    .returning(Conversation.id)
                )
            else:
                written = await s.scalar(
                    update(Conversation)
                    .where(
                        Conversation.id == key,
                        Conversation.revision == expected_revision,
                    )
                    .values(**values)
                    .returning(Conversation.id)
                )

            if written is None:
                raise ConversationConflict(record.conversation_id)

            s.add_all(message.row(key) for message in stored)

            if (
                record.head_id is not None
                and record.head_id not in new_ids
                and await s.scalar(
                    select(Message.id).where(Message.id == record.head_id)
                )
                is None
            ):
                raise ConversationConflict(record.conversation_id)

            _announce(s, record.scope)

        self._keep(stored)

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
        return (await self.read_history(entry_id, limit=1))[0]

    @override
    async def read_history(
        self, head_id: str | None, *, limit: int | None = None
    ) -> list[ConversationEntry]:
        """The entries up to *head_id*, cached or walked up their parents in one recursive query."""
        if head_id is None or limit == 0:
            return []

        path, missed = self._cached_path(head_id, limit)
        older = (
            []
            if missed is None
            else await self._read_path(
                missed, None if limit is None else limit - len(path)
            )
        )

        return [message.entry() for message in (*older, *reversed(path))]


store = PostgresConversationStore()
"""The conversations of this app."""
