"""The queries the app adds beside the store.

The functions below keep what the app adds beside the store: the owner check,
the title, the list, the full-text search, and imports.  The owner is the
store's ``scope``, so a conversation saved for another user loads as absent.
"""

import asyncio
import contextlib
from collections.abc import Callable, Collection, Iterable, Sequence
from datetime import datetime
from functools import reduce
from typing import Any, TypedDict, Unpack
from uuid import UUID

from pydantic_ai.conversation import Conversation as RunConversation
from pydantic_ai.conversation_store import ConversationConflict, ConversationRecord
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage
from sqlalchemy import (
    ColumnElement,
    Row,
    Text,
    and_,
    bindparam,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY

from ...config import settings
from ...shared.l10n import Localized
from ...store import User
from .._common import affected_rows
from ..engine import session, transaction
from ..models import Conversation
from .messages import UNTITLED, extract_title
from .models import (
    ConversationData,
    ConversationSummary,
    OwnedConversation,
    _conversation,
)
from .postgres import _announce, store

__all__ = [
    "claim_conversation",
    "conversation_owners",
    "import_conversation",
    "list_conversations",
    "load_conversation",
    "load_conversation_data",
    "load_conversation_summary",
    "open_conversation",
    "owned_conversation",
    "owned_conversations",
    "remove_conversation",
    "remove_owned_conversations",
    "search_conversation",
    "set_conversation_title",
]

_MAX_RANKED_CHARS = 100_000
"""How much of one passage a search ranks, well below the 1 MB a ``tsvector`` holds."""

_NO_MESSAGES = Localized(
    en="conversation export has no messages",
    de="Der Konversationsexport enthält keine Nachrichten",
)


def _owned(owner: str) -> ColumnElement[bool]:
    """Match the conversations *owner* owns."""
    return Conversation.user_id == owner


def _row(conversation: OwnedConversation) -> ColumnElement[bool]:
    """Match *conversation*'s row, still only while its user owns it."""
    return and_(Conversation.id == conversation.id, _owned(conversation.owner))


async def _owner_of(conversation_id: UUID) -> str | None:
    """The id of the user owning *conversation_id*, ``None`` while no record holds it."""
    return await store.owner_of(str(conversation_id))


async def conversation_owners(conversation_ids: Collection[UUID]) -> dict[UUID, str]:
    """The owner of each of *conversation_ids* a record holds, in one query."""
    return await store.owners_of(conversation_ids)


async def owned_conversation(
    user: User, conversation_id: UUID
) -> OwnedConversation | None:
    """The one ownership check: *conversation_id* as *user*'s, or ``None``.

    Refused to a service principal, whose one identity holds the conversations
    of everyone it answers, so it can only run its turns (:func:`open_conversation`).

    Raises:
        AccessDenied: For a service principal.
    """
    user.require_person()

    return (
        _conversation(user.id, conversation_id)
        if await _owner_of(conversation_id) == user.id
        else None
    )


async def open_conversation(
    user: User, conversation_id: UUID
) -> tuple[OwnedConversation, bool] | None:
    """*conversation_id* as *user*'s and whether its record exists, or ``None`` for another user's."""
    owner = await _owner_of(conversation_id)

    if owner is not None and owner != user.id:
        return None

    return _conversation(user.id, conversation_id), owner is not None


async def claim_conversation(
    conversation: OwnedConversation, *, title: str | None = None
) -> RunConversation | None:
    """Store *conversation*'s empty record unless its id is taken, ``None`` when it is another user's.

    The store's insert of a new record decides between racing claims: the
    second finds the record there, which is the caller's only when they own
    it.  A new record takes *title*, so the conversation is listed under it
    while its first turn still runs.  Returns the conversation to run on.
    """
    record = ConversationRecord(
        conversation_id=conversation.key,
        head_id=None,
        scope=conversation.owner,
        title=title,
    )

    try:
        await store.write_record(record, expected_revision=None)
    except ConversationConflict:
        return await load_conversation(conversation)

    return RunConversation(
        conversation_id=record.conversation_id, revision=record.revision
    )


_SUMMARY = (
    Conversation.id,
    Conversation.title,
    Conversation.created_at,
    Conversation.updated_at,
)
"""The columns of a summary, leaving out the record's JSON."""


def _to_summary(row: Row[UUID, str | None, datetime, datetime]) -> ConversationSummary:
    key, title, created_at, updated_at = row.tuple()

    return ConversationSummary(
        id=key, title=title or "", created_at=created_at, updated_at=updated_at
    )


async def load_conversation(conversation: OwnedConversation) -> RunConversation | None:
    """The conversation a run continues, ``None`` before it is claimed."""
    return await store.load(conversation.key, scope=conversation.owner)


async def load_conversation_summary(
    conversation: OwnedConversation,
) -> ConversationSummary | None:
    """Return one conversation's summary, or ``None`` before it is claimed."""
    async with session() as s:
        row = (
            await s.execute(select(*_SUMMARY).where(_row(conversation)))
        ).one_or_none()

    return None if row is None else _to_summary(row)


async def load_conversation_data(
    conversation: OwnedConversation,
) -> ConversationData | None:
    """The active history with the summary, ``None`` before the conversation is claimed."""
    summary, loaded = await asyncio.gather(
        load_conversation_summary(conversation), load_conversation(conversation)
    )

    if summary is None or loaded is None:
        return None

    return ConversationData(**summary.model_dump(), messages=loaded.messages)


async def list_conversations(
    user: User, *, running: Collection[UUID] = ()
) -> list[ConversationSummary]:
    """List a user's non-empty conversations newest first.

    A conversation whose first turn still runs has no message stored yet, so
    the ids of the *running* ones are listed too.

    Raises:
        AccessDenied: For a service principal, as :func:`owned_conversation`.
    """
    user.require_person()

    async with session() as s:
        rows = (
            await s.execute(
                select(*_SUMMARY)
                .where(
                    _owned(user.id),
                    Conversation.head_id.is_not(None)
                    | Conversation.id.in_(list(running)),
                )
                .order_by(Conversation.updated_at.desc())
            )
        ).all()

    return [_to_summary(row) for row in rows]


async def owned_conversations(user: User) -> list[OwnedConversation]:
    """Return every conversation *user* owns."""
    async with session() as s:
        ids = (await s.scalars(select(Conversation.id).where(_owned(user.id)))).all()

    return [_conversation(user.id, cid) for cid in ids]


def _fts[T](
    build: Callable[[str, T], ColumnElement[Any]], value: T
) -> ColumnElement[Any]:
    """*build* once per configured FTS language, combined with ``||``.

    The same union the sparse channel indexes chunks with, so a conversation
    is stemmed like the documents it discusses.
    """
    return reduce(
        lambda acc, term: acc.op("||")(term),
        (build(cfg, value) for cfg in settings.embedding.text_search_config),
    )


def _ranked(text: str) -> str:
    """The part of *text* a search ranks, without the NUL a PostgreSQL text refuses."""
    return text[:_MAX_RANKED_CHARS].replace("\x00", "")


async def search_conversation(
    conversation: OwnedConversation,
    query: str,
    render: Callable[[ModelMessage], Iterable[str]],
    *,
    limit: int,
) -> list[tuple[int, str]]:
    """The active history's passages that match *query*, best first.

    *render* splits a message into its searchable passages, and each match is
    the position of its message in the history paired with the passage.
    Ranked by PostgreSQL full-text search at query time rather than from an
    index, since a conversation is searched one at a time and its history is
    loaded anyway.
    """
    loaded = await load_conversation(conversation)
    passages = [
        (position, passage)
        for position, message in enumerate(loaded.messages if loaded else [])
        for passage in render(message)
    ]

    if not passages:
        return []

    texts = (
        func.unnest(
            bindparam(
                "texts",
                [_ranked(text) for _, text in passages],
                type_=ARRAY(Text),
            )
        )
        .table_valued("text", with_ordinality="ordinal")
        .render_derived()
    )
    document = _fts(func.to_tsvector, texts.c.text)
    tsquery = _fts(func.plainto_tsquery, query)

    async with session() as s:
        ordinals = await s.scalars(
            select(texts.c.ordinal)
            .where(document.op("@@")(tsquery))
            .order_by(func.ts_rank(document, tsquery).desc(), texts.c.ordinal)
            .limit(limit)
        )

    return [passages[ordinal - 1] for ordinal in ordinals]


class TitleGuard(TypedDict, total=False):
    """What a title change expects of the conversation."""

    expected_title: str | None
    """The title it must still have, ``None`` for none."""


async def set_conversation_title(
    conversation: OwnedConversation, title: str, **guard: Unpack[TitleGuard]
) -> ConversationSummary | None:
    """Set the title on *conversation*, returning its summary, ``None`` while no record holds it.

    With ``expected_title``, only while it still has that title, so a title set
    since wins, which the summary then names.
    """
    # Covers `ConversationConflict`, a title set since, as well as an absent record.
    with contextlib.suppress(UserError):
        await store.update(
            conversation.key, title=title, scope=conversation.owner, **guard
        )

    return await load_conversation_summary(conversation)


async def remove_conversation(conversation: OwnedConversation) -> bool:
    """Delete *conversation* (cascades to its messages), returning whether it had a record."""
    async with session() as s:
        result = await s.execute(delete(Conversation).where(_row(conversation)))
        _announce(s, conversation.owner)

    return affected_rows(result) > 0


async def remove_owned_conversations(user: User) -> None:
    """Delete every conversation *user* owns (cascades to their messages)."""
    async with session() as s:
        _ = await s.execute(delete(Conversation).where(_owned(user.id)))
        _announce(s, user.id)


async def import_conversation(
    conversation: OwnedConversation,
    messages: Sequence[ModelMessage],
    *,
    title: str | None = None,
) -> ConversationSummary | None:
    """Store *messages* as the whole history of *conversation*, replacing any it had.

    *messages* is an exported active history, already decoded to model
    messages by the caller, so branches are not restored.  When *title* is
    omitted it is derived from the first user message.  The replacement is one
    transaction, so a failed import leaves the conversation as it was.

    Returns:
        The imported conversation, ``None`` when another user holds its id.

    Raises:
        ValueError: if *messages* is empty.
    """
    if not messages:
        raise ValueError(_NO_MESSAGES.current)

    title = title or extract_title(messages) or UNTITLED.current

    async with transaction():
        await remove_conversation(conversation)
        claimed = await claim_conversation(conversation, title=title)

        if claimed is None:
            return None

        claimed.messages = list(messages)
        await store.save(claimed, scope=conversation.owner)

        return await load_conversation_summary(conversation)
