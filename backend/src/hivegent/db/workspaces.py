"""Workspace repository: the row of each workspace, which its documents name.

A workspace's row is materialised lazily the first time a document lands in
it, below the user, group, or source row it belongs to, if any, and goes with
that parent or a wipe of the workspace, taking its documents along.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..store import GlobalScope, GroupScope, SourceScope, UserScope, WorkspaceScope
from ..store import Workspace as Store
from ._common import ensure_row
from .engine import session
from .groups import ensure_group
from .models import Workspace
from .sources import ensure_source
from .users import ensure_user

__all__ = [
    "delete_all_workspaces",
    "delete_workspace",
    "ensure_workspace",
    "list_workspace_keys",
]


async def ensure_workspace(s: AsyncSession, scope: WorkspaceScope) -> None:
    """Materialise the row of *scope*'s workspace, and its parent's, before a document lands in it."""
    match scope:
        case GlobalScope():
            await ensure_row(s, Workspace, key=scope.key)
        case UserScope(id=user_id):
            await ensure_user(s, user_id)
            await ensure_row(s, Workspace, key=scope.key, user_id=user_id)
        case GroupScope(id=group_id):
            await ensure_group(s, group_id)
            await ensure_row(s, Workspace, key=scope.key, group_id=group_id)
        case SourceScope(id=source_id):
            await ensure_source(s, source_id)
            await ensure_row(s, Workspace, key=scope.key, source_id=source_id)


async def list_workspace_keys() -> set[str]:
    """The key of every workspace with a row."""
    async with session() as s:
        return set(await s.scalars(select(Workspace.key)))


async def delete_all_workspaces() -> None:
    """Delete every workspace row, which takes every document and chunk along."""
    async with session() as s:
        await s.execute(delete(Workspace))


async def delete_workspace(store: Store) -> None:
    """Delete the row of *store*, which takes its documents and chunks along.

    Raises:
        AccessDenied: When *store* is not granted :attr:`~hivegent.store.Grant.WRITE`.
    """
    store.require_writable()

    async with session() as s:
        await s.execute(delete(Workspace).where(Workspace.key == store.store_key))
