"""Group repository: identity rows, membership lookups, admin overviews.

Group rows mirror the OIDC ``groups`` claim (see
:mod:`hivegent.auth`) and are materialised lazily the first time a
group is referenced.  This module owns every group-specific table
operation; cross-cutting helpers stay in :mod:`._common`.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows
from .engine import session
from .models import Document, Group, GroupMember

__all__ = [
    "delete_all_groups",
    "ensure_group",
    "list_group_ids",
    "list_groups_with_counts",
]


async def ensure_group(s: AsyncSession, group_id: str) -> None:
    """Materialise a :class:`Group` row lazily before a dependent insert."""
    if await s.get(Group, group_id) is None:
        s.add(Group(id=group_id))


async def list_group_ids() -> frozenset[str]:
    """Return the set of every group ID registered in the database."""
    async with session() as s:
        result = await s.execute(select(Group.id))
        return frozenset(result.scalars().all())


async def list_groups_with_counts() -> list[tuple[str, int, int]]:
    """Return ``(group_id, document_count, member_count)`` for every group.

    Used by the admin overview to size each group's footprint before a
    targeted wipe.  Counts run in a single query each so the call is
    cheap even for many groups.
    """
    docs_subq = (
        select(
            Document.owner_group_id.label("gid"),
            func.count().label("n"),
        )
        .where(Document.owner_group_id.is_not(None))
        .group_by(Document.owner_group_id)
        .subquery()
    )
    members_subq = (
        select(
            GroupMember.group_id.label("gid"),
            func.count().label("n"),
        )
        .group_by(GroupMember.group_id)
        .subquery()
    )
    async with session() as s:
        rows = (
            await s.execute(
                select(
                    Group.id,
                    func.coalesce(docs_subq.c.n, 0),
                    func.coalesce(members_subq.c.n, 0),
                )
                .outerjoin(docs_subq, docs_subq.c.gid == Group.id)
                .outerjoin(members_subq, members_subq.c.gid == Group.id)
                .order_by(Group.id)
            )
        ).all()
    return [(gid, int(docs), int(members)) for gid, docs, members in rows]


async def delete_all_groups() -> int:
    """Delete every group row.  Cascades to memberships and group documents."""
    async with session() as s:
        result = await s.execute(delete(Group))
    return affected_rows(result)
