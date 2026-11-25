"""Group repository: identity rows.

Group rows model shared knowledge containers (see :mod:`hivegent.auth`)
and are materialised lazily the first time a group is referenced.
Membership is not stored.  It lives solely in the OIDC token.  This
module owns every group-specific table operation; cross-cutting helpers
stay in :mod:`._common`.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import ensure_row
from .engine import session
from .models import Document, Group, Workspace

__all__ = [
    "ensure_group",
    "list_group_ids",
    "list_groups_with_counts",
]


async def ensure_group(s: AsyncSession, group_id: str) -> None:
    """Materialise a :class:`Group` row lazily before a dependent insert."""
    await ensure_row(s, Group, id=group_id)


async def list_group_ids() -> frozenset[str]:
    """Return the set of group IDs registered in the database."""
    async with session() as s:
        result = await s.execute(select(Group.id))
        return frozenset(result.scalars().all())


async def list_groups_with_counts() -> list[tuple[str, int]]:
    """Return ``(group_id, document_count)`` for every group row, by id."""
    documents = (
        select(Workspace.group_id.label("gid"), func.count().label("n"))
        .join(Document, Document.workspace_key == Workspace.key)
        .where(Workspace.group_id.is_not(None))
        .group_by(Workspace.group_id)
        .subquery()
    )

    async with session() as s:
        rows = await s.execute(
            select(Group.id, func.coalesce(documents.c.n, 0))
            .outerjoin(documents, documents.c.gid == Group.id)
            .order_by(Group.id)
        )

    return [(gid, int(docs)) for gid, docs in rows]
