"""Conversation repository — a server-authoritative message tree.

The database is the source of truth for history.  Each turn loads the
active-path prefix from SQL, runs the agent on it, and appends the turn's
new messages as a chain under a fork point (:func:`append_branch`).  Editing
or regenerating forks a sibling chain instead of overwriting, so prior
branches are preserved; the linear history the frontend sees is the *active
path* — the conversation's newest leaf walked up to the root via
``Message.parent_id`` (:func:`_load_active_path`).  The active branch is just
the most recently appended one, so no branch pointer is stored.

Because history is loaded from SQL rather than echoed by the browser, stored
``ToolReturnPart.metadata`` (the ``DataChunk`` tool-output payload the frontend
renders) is never stripped; :func:`_load_messages` only re-types it from the
stored JSON on the way back out.
"""

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
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
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from sqlalchemy import CTE, ColumnElement, delete, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ._common import affected_rows, new_id
from .engine import session
from .models import Conversation, Message
from .users import ensure_user

__all__ = [
    "ConversationData",
    "ConversationSummary",
    "MessagePair",
    "append_branch",
    "conversation_exists",
    "create_compacted_conversation",
    "delete_all_conversations",
    "extract_title",
    "import_conversation",
    "is_user_request",
    "list_conversations",
    "load_active_for_display",
    "load_conversation",
    "load_conversation_summary",
    "remove_conversation",
    "resolve_fork",
    "set_conversation_title",
]


@dataclass(frozen=True, slots=True)
class ActiveNode:
    """A node id, its parent id, and decoded message, in root->leaf order."""

    id: str
    parent_id: str | None
    message: ModelMessage


# A node id paired with its decoded message — the public active-path shape.
type MessagePair = tuple[str, ModelMessage]


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
    compacted_from: str | None = None


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


def is_user_request(msg: ModelMessage) -> bool:
    """Whether *msg* is a user turn (a request carrying a user prompt).

    Tool-return-only requests in the middle of an agent loop are not user
    turns, so a regenerate forks from the nearest message this selects.
    """
    return isinstance(msg, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in msg.parts
    )


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


# ─── Active-path traversal ─────────────────────────────────────────────


def _newest_leaf_id(conversation_id: str) -> ColumnElement[str]:
    """Scalar subquery of a conversation's active leaf: its newest childless node.

    A turn appends its messages as one chain in a single transaction, so they
    routinely share a ``created_at`` to the microsecond — ordering by timestamp
    alone could then pick a mid-chain node (e.g. the tool-return request) over
    the turn's final response and silently truncate the active path.  Restricting
    to a *leaf* (a node nothing forks from) removes that ambiguity: within a chain
    only the tip is childless, and distinct branches' tips are created far enough
    apart that the newest leaf is unambiguously the active one; ``id`` breaks the
    otherwise-impossible exact-timestamp tie so the pick stays stable.
    """
    child = aliased(Message)
    is_leaf = ~select(literal(1)).where(child.parent_id == Message.id).exists()
    return (
        select(Message.id)
        .where(Message.conversation_id == conversation_id, is_leaf)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def _active_path_cte(conversation_id: str, leaf_id: str | ColumnElement[str]) -> CTE:
    """Recursive CTE of the nodes from *leaf_id* up to the root.

    *leaf_id* is the active leaf — either an explicit node id or the scalar
    subquery selecting the conversation's newest leaf.  The anchor is a plain
    equality (no ordering or limit inside the recursive term), and walking *up*
    the unique ``parent_id`` is provably terminating: ``parent_id`` only ever
    points at an already-created node, so the chain strictly recedes in time and
    can neither cycle nor escape the conversation (the recursive term re-checks
    ``conversation_id``).  Columns: ``id``, ``parent_id``, ``payload``, ``depth``
    (0 at the leaf).
    """
    anchor = (
        select(
            Message.id,
            Message.parent_id,
            Message.payload,
            literal(0).label("depth"),
        )
        .where(Message.id == leaf_id, Message.conversation_id == conversation_id)
        .cte(recursive=True)
    )
    parent = aliased(Message)
    return anchor.union_all(
        select(
            parent.id,
            parent.parent_id,
            parent.payload,
            anchor.c.depth + 1,
        ).where(
            parent.id == anchor.c.parent_id,
            parent.conversation_id == conversation_id,
        )
    )


async def _load_active_path(
    s: AsyncSession, conversation_id: str, leaf_id: str | None = None
) -> list[ActiveNode]:
    """Walk a leaf up to the root via ``parent_id``, returned root->leaf.

    *leaf_id* defaults to the conversation's newest leaf (the active tip); an
    explicit id anchors at another branch's tip.  An absent or unmatched leaf
    yields an empty path.
    """
    cte = _active_path_cte(conversation_id, leaf_id or _newest_leaf_id(conversation_id))
    rows = (
        await s.execute(
            select(cte.c.id, cte.c.parent_id, cte.c.payload).order_by(
                cte.c.depth.desc()
            )
        )
    ).all()
    messages = _load_messages([row[2] for row in rows])
    return [
        ActiveNode(row[0], row[1], msg) for row, msg in zip(rows, messages, strict=True)
    ]


async def _sibling_map(
    s: AsyncSession, conversation_id: str, path: Sequence[ActiveNode]
) -> dict[str, list[str]]:
    """Map each active node that has siblings to its ordered sibling ids.

    Only the children of the active path's parents are fetched (those are the
    siblings), not the whole conversation tree.  Branch index and count are
    derived from this list where the wire projection happens.
    """
    active_parents = {node.parent_id for node in path}
    if not active_parents:
        return {}

    branch = [Message.parent_id == p for p in active_parents]
    rows = (
        await s.execute(
            select(Message.id, Message.parent_id)
            .where(Message.conversation_id == conversation_id, or_(*branch))
            .order_by(Message.created_at)
        )
    ).all()
    children: dict[str | None, list[str]] = {}
    for node_id, parent_id in rows:
        children.setdefault(parent_id, []).append(node_id)

    return {
        node.id: siblings
        for node in path
        if len(siblings := children.get(node.parent_id, [])) > 1
    }


async def _display(
    s: AsyncSession, conversation_id: str
) -> tuple[list[MessagePair], dict[str, list[str]]]:
    path = await _load_active_path(s, conversation_id)
    pairs = [(node.id, node.message) for node in path]
    return pairs, await _sibling_map(s, conversation_id, path)


def _has_messages() -> ColumnElement[bool]:
    """Whether a conversation has any message (excludes empty conversations).

    Served by the ``(conversation_id, created_at)`` index, so it is a cheap
    existence check with no row scan.
    """
    return select(literal(1)).where(Message.conversation_id == Conversation.id).exists()


def _to_summary(conv: Conversation) -> ConversationSummary:
    """Build a list-view summary."""
    return ConversationSummary(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
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
    """Return full conversation data (active path), or ``None`` if not owned."""
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
        if conv is None:
            return None
        path = await _load_active_path(s, conversation_id)
    return ConversationData(
        id=conv.id,
        title=conv.title or "",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[node.message for node in path],
        compacted_from=conv.compacted_from_id,
    )


async def load_active_for_display(
    user_id: str, conversation_id: str
) -> tuple[list[MessagePair], dict[str, list[str]]] | None:
    """Return the active path as ``(node_id, message)`` pairs plus sibling map.

    The node ids anchor each ``UIMessage.id`` so the client can address a
    message for edit / regenerate; the sibling map lists each forking node's
    siblings.  ``None`` if the conversation is missing or not owned.
    """
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
        if conv is None:
            return None
        return await _display(s, conversation_id)


async def list_conversations(user_id: str) -> list[ConversationSummary]:
    """List a user's non-empty conversations newest first."""
    async with session() as s:
        convs = (
            await s.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id, _has_messages())
                .order_by(Conversation.updated_at.desc())
            )
        ).all()
    return [_to_summary(conv) for conv in convs]


async def load_conversation_summary(
    user_id: str, conversation_id: str
) -> ConversationSummary | None:
    """Return one conversation's summary, or ``None`` if missing or not owned."""
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
    return None if conv is None else _to_summary(conv)


async def conversation_exists(user_id: str, conversation_id: str) -> bool:
    """Return whether *conversation_id* exists and is owned by *user_id*."""
    async with session() as s:
        owner = await s.scalar(
            select(Conversation.user_id).where(Conversation.id == conversation_id)
        )
    return owner == user_id


# ─── Writes ────────────────────────────────────────────────────────────


async def _lock(s: AsyncSession, conversation_id: str) -> None:
    """Serialise concurrent writes to one conversation for this transaction.

    The fork-load / append / active-leaf sequences below are read-then-write,
    so two racing turns (duplicate submit, multiple tabs) would otherwise
    interleave.  The advisory lock auto-releases on commit/rollback.
    """
    await s.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(conversation_id, 0)))
    )


def _fork_for_path(
    path: Sequence[ActiveNode], *, regenerate: bool, message_id: str | None
) -> tuple[list[ModelMessage], str | None]:
    """Resolve the (prefix, fork-parent) for a turn over a loaded active path.

    The new branch is appended under the returned fork node, and the run
    replays the returned prefix as history:

    - regenerate: fork at the nearest user request at/above the target node;
      the prefix ends with that user turn and no new client message follows.
    - edit (submit + the node id of a user turn): fork at the edited node's
      parent; the prefix stops before it, so the edited node and its subtree
      become a sibling.
    - plain submit: fork at the active leaf, continuing the conversation.

    A submit whose *message_id* does not name a user turn on the active path is
    a continuation, not an edit: the AI SDK also sends the last message's id
    when it auto-continues after a tool approval, and a client can address a
    message this server never persisted (a turn that failed before its first
    write).  Both fall through to the leaf.
    """
    ids = [node.id for node in path]
    msgs = [node.message for node in path]

    if not ids:
        return [], None

    if regenerate:
        idx = ids.index(message_id) if message_id in ids else len(ids) - 1
        for i in range(idx, -1, -1):
            if is_user_request(msgs[i]):
                return msgs[: i + 1], ids[i]
        return msgs, ids[-1]

    if message_id in ids:
        idx = ids.index(message_id)

        if is_user_request(msgs[idx]):
            return (msgs[:idx], ids[idx - 1]) if idx > 0 else ([], None)

    return msgs, ids[-1]


async def resolve_fork(
    user_id: str, conversation_id: str, *, regenerate: bool, message_id: str | None
) -> tuple[list[ModelMessage], str | None]:
    """Return the history prefix and fork-parent node for the next turn.

    Reads the active path; a missing or unowned conversation (or one with no
    messages yet) forks at the root with an empty prefix.
    """
    async with session() as s:
        conv = await _get_owned(s, user_id, conversation_id)
        if conv is None:
            return [], None
        path = await _load_active_path(s, conversation_id)
    return _fork_for_path(path, regenerate=regenerate, message_id=message_id)


async def append_branch(
    user_id: str,
    conversation_id: str,
    parent_id: str | None,
    messages: Sequence[ModelMessage],
    *,
    head_id: str | None = None,
    title: str | None = None,
) -> str | None:
    """Append *messages* as a chain under *parent_id* and make it active.

    *messages* is the turn's delta (its new messages past the loaded prefix).
    Each becomes a node chained by ``parent_id``; the last one is the newest
    message and therefore the new active leaf, so the turn extends the active
    path (plain submit) or forks a sibling branch (edit / regenerate) without
    touching the preserved prior branches.  Empty delta is a no-op.  The
    conversation row is created lazily on the first turn, titled by *title* or,
    when omitted, derived from the first user message.  Raises on failure so
    the caller can surface a hard error rather than silently lose the turn.

    *head_id* forces the id of the first node instead of minting one, so the
    chat route can announce the new user message's id before the turn is
    persisted (see ``X-Message-Id`` in ``backend/README.md``).
    """
    msg_list = list(messages)
    if not msg_list:
        return None

    async with session() as s:
        await _lock(s, conversation_id)
        await ensure_user(s, user_id)

        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            conv = Conversation(id=conversation_id, user_id=user_id)
            s.add(conv)
        elif conv.user_id != user_id:
            raise PermissionError(
                f"conversation {conversation_id} is not owned by {user_id}"
            )

        parent = parent_id
        last_id: str | None = None
        for index, payload in enumerate(_dump_messages(msg_list)):
            node_id = head_id if index == 0 and head_id else new_id()
            s.add(
                Message(
                    id=node_id,
                    conversation_id=conversation_id,
                    parent_id=parent,
                    payload=payload,
                )
            )
            parent = last_id = node_id

        if conv.title is None:
            conv.title = title or extract_title(msg_list)
        conv.updated_at = datetime.now(UTC)
    return last_id


async def set_conversation_title(
    user_id: str, conversation_id: str, title: str
) -> ConversationSummary | None:
    """Set the title on a conversation owned by *user_id*."""
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


async def import_conversation(
    user_id: str,
    messages: Sequence[ModelMessage],
    *,
    title: str | None = None,
) -> ConversationSummary:
    """Persist *messages* as a fresh single-branch conversation for *user_id*.

    *messages* is a conversation's active path (the Vercel AI UI messages an
    export carries, already decoded to model messages by the caller). It is
    stored as one linear chain under fresh ids, so re-importing the same export
    never collides. Branch structure is not part of a client-side export, so
    only the visible path is restored. When *title* is omitted it is derived
    from the first user message.

    Raises:
        ValueError: if *messages* is empty.
    """
    if not messages:
        raise ValueError("conversation export has no messages")

    conversation_id = new_id()
    await append_branch(user_id, conversation_id, None, messages, title=title)

    summary = await load_conversation_summary(user_id, conversation_id)
    if summary is None:  # unreachable: the conversation was just created
        raise ValueError("failed to import conversation")

    return summary


async def create_compacted_conversation(
    user_id: str,
    original_conversation_id: str,
    summary_message: ModelMessage,
    title: str,
) -> str:
    """Persist a fresh conversation seeded with one summary message as its root.

    The source is always a persisted row, since its messages are what was
    summarized, so the ``compacted_from`` link is never absent.
    """
    conversation_id = new_id()
    node_id = new_id()
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
                id=node_id,
                conversation_id=conversation_id,
                parent_id=None,
                payload=_dump_messages([summary_message])[0],
            )
        )
    return conversation_id
