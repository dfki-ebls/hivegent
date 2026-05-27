"""User repository: identity rows and admin overviews.

Hivegent is not an identity store — :class:`User` rows are materialised
lazily the first time an authenticated identity stores anything in the
local database.  This module only exposes the small set of user-table
operations that are local to the app (lazy insert, admin overview,
global wipe).  Authentication and group membership live in
:mod:`hivegent.auth` and the OIDC IdP.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows
from .engine import session
from .models import Conversation, Document, User

__all__ = [
    "delete_all_users",
    "delete_user",
    "ensure_user",
    "list_users_with_counts",
]


async def ensure_user(s: AsyncSession, user_id: str) -> None:
    """Materialise a :class:`User` row lazily before a dependent insert."""
    if await s.get(User, user_id) is None:
        s.add(User(id=user_id))


async def list_users_with_counts() -> list[tuple[str, str | None, str | None, int, int]]:
    """Return ``(user_id, email, display_name, document_count, conversation_count)``.

    Only rows materialised in the local database are returned — Hivegent
    is not an identity store and does not enumerate accounts that have
    not yet interacted with the app.
    """
    docs_subq = (
        select(
            Document.owner_user_id.label("uid"),
            func.count().label("n"),
        )
        .where(Document.owner_user_id.is_not(None))
        .group_by(Document.owner_user_id)
        .subquery()
    )
    convs_subq = (
        select(
            Conversation.user_id.label("uid"),
            func.count().label("n"),
        )
        .group_by(Conversation.user_id)
        .subquery()
    )
    async with session() as s:
        rows = (
            await s.execute(
                select(
                    User.id,
                    User.email,
                    User.display_name,
                    func.coalesce(docs_subq.c.n, 0),
                    func.coalesce(convs_subq.c.n, 0),
                )
                .outerjoin(docs_subq, docs_subq.c.uid == User.id)
                .outerjoin(convs_subq, convs_subq.c.uid == User.id)
                .order_by(User.id)
            )
        ).all()
    return [
        (uid, email, display_name, int(docs), int(convs))
        for uid, email, display_name, docs, convs in rows
    ]


async def delete_all_users() -> int:
    """Delete every user row.  Cascades to tokens, memory, conversations, documents."""
    async with session() as s:
        result = await s.execute(delete(User))
    return affected_rows(result)


async def delete_user(user_id: str) -> bool:
    """Delete one user row.  Cascades to tokens, memory, conversations, documents.

    Returns ``True`` if a row was deleted.  The user can re-materialise
    via :func:`ensure_user` on the next request, so this is the right
    primitive for "wipe all data for this user".
    """
    async with session() as s:
        result = await s.execute(delete(User).where(User.id == user_id))
    return affected_rows(result) > 0
