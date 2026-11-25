"""User repository: identity rows.

Hivegent is not an identity store.  :class:`User` rows are materialised
lazily the first time an authenticated identity stores anything in the
local database.  This module only exposes the small set of user-table
operations that are local to the app (lazy insert, existence check,
wipes).  Authentication and group membership live in
:mod:`hivegent.auth` and the OIDC IdP.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows, ensure_row
from .engine import session
from .models import Conversation, Document, Group, User, Workspace

__all__ = [
    "delete_all_identities",
    "delete_user",
    "ensure_user",
    "list_users_with_counts",
    "load_preferences",
    "update_preferences",
    "user_exists",
]


async def ensure_user(s: AsyncSession, user_id: str) -> None:
    """Materialise a :class:`User` row lazily before a dependent insert."""
    await ensure_row(s, User, id=user_id)


async def load_preferences(user_id: str) -> dict[str, Any]:
    """The choices *user_id* stored for their runs, none for a user without a row."""
    async with session() as s:
        return await s.scalar(select(User.preferences).where(User.id == user_id)) or {}


async def update_preferences(
    user_id: str, changed: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge *changed* into the choices *user_id* stored, keeping the others, and return them all."""
    insert = pg_insert(User).values(id=user_id, preferences=dict(changed))

    async with session() as s:
        stored = await s.scalar(
            insert.on_conflict_do_update(
                index_elements=[User.id],
                set_={"preferences": User.preferences.op("||")(insert.excluded.preferences)},
            ).returning(User.preferences)
        )

    return stored or {}


async def user_exists(user_id: str) -> bool:
    """Whether a user has left a footprint in the local database.

    Reads only the user's own ``id`` and never group state, which lives
    solely in the OIDC token.  Used to reject acting on an identity the
    app has never seen.
    """
    async with session() as s:
        return await s.get(User, user_id) is not None


async def list_users_with_counts() -> list[tuple[str, int, int]]:
    """Return ``(user_id, document_count, conversation_count)`` for every user row, by id.

    Only users who stored something appear, since Hivegent is not an identity store.
    """
    documents = (
        select(Workspace.user_id.label("uid"), func.count().label("n"))
        .join(Document, Document.workspace_key == Workspace.key)
        .where(Workspace.user_id.is_not(None))
        .group_by(Workspace.user_id)
        .subquery()
    )
    conversations = (
        select(Conversation.user_id.label("uid"), func.count().label("n"))
        .group_by(Conversation.user_id)
        .subquery()
    )

    async with session() as s:
        rows = await s.execute(
            select(
                User.id,
                func.coalesce(documents.c.n, 0),
                func.coalesce(conversations.c.n, 0),
            )
            .outerjoin(documents, documents.c.uid == User.id)
            .outerjoin(conversations, conversations.c.uid == User.id)
            .order_by(User.id)
        )

    return [(uid, int(docs), int(convs)) for uid, docs, convs in rows]


async def delete_all_identities() -> None:
    """Delete every user and group row in one transaction, so a crash leaves neither half.

    Users cascade to memory, conversations (and their messages), and their
    workspace with its documents, groups to theirs, and chunks with every
    document.
    """
    async with session() as s:
        await s.execute(delete(User))
        await s.execute(delete(Group))


async def delete_user(user_id: str) -> bool:
    """Delete one user row.  Cascades to memory, conversations, and the workspace.

    Returns ``True`` if a row was deleted.  The user can re-materialise
    via :func:`ensure_user` on the next request, so this is the right
    primitive for "wipe all data for this user".
    """
    async with session() as s:
        result = await s.execute(delete(User).where(User.id == user_id))
    return affected_rows(result) > 0
