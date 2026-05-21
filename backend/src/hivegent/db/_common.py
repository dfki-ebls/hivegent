"""Small helpers shared across repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import session
from .models import Document, Group, User, _nid

__all__ = [
    "affected_rows",
    "ensure_group",
    "ensure_user",
    "list_group_ids",
    "new_id",
    "stem_subtree_filter",
]


def affected_rows(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


new_id = _nid


async def ensure_user(s: AsyncSession, user_id: str) -> None:
    """Materialise a :class:`User` row lazily before a dependent insert."""
    if await s.get(User, user_id) is None:
        s.add(User(id=user_id))


async def ensure_group(s: AsyncSession, group_id: str) -> None:
    """Materialise a :class:`Group` row lazily before a dependent insert."""
    if await s.get(Group, group_id) is None:
        s.add(Group(id=group_id))


async def list_group_ids() -> frozenset[str]:
    """Return the set of every group ID registered in the database."""
    async with session() as s:
        result = await s.execute(select(Group.id))
        return frozenset(result.scalars().all())


def stem_subtree_filter(prefix: str):
    """WHERE matching ``stem_path == prefix`` or ``stem_path LIKE 'prefix/%'``."""
    return or_(Document.stem_path == prefix, Document.stem_path.like(prefix + "/%"))
