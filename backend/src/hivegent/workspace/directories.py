"""Store-wide wipes.

Creating, moving, and deleting a directory are changeset items
(:mod:`~hivegent.workspace.changeset`), so they commit atomically with whatever
else a change does.  The store-wide wipe takes the lock with ``whole_store`` so
it cannot race an entry still indexing or a bulk import.
"""

import asyncio

from ..config import settings
from ..db.workspaces import delete_workspace
from ..files import remove_path
from ..store import Workspace
from ..workspace_events import announce_workspace_changed
from .locks import root_lock
from .operations import Root

__all__ = [
    "delete_all",
    "delete_workspace_root",
]


async def delete_all(store: Workspace) -> None:
    """Wipe every trace of a workspace: its files and its row.

    Its documents and their chunks cascade-delete with the row, which the next
    document to land materialises again, so no reconcile finds a row without its folder.
    """
    async with root_lock(Root.of(store)):
        await delete_workspace(store)
        await asyncio.to_thread(remove_path, store.path)

    announce_workspace_changed((store,))


async def delete_workspace_root() -> None:
    """Wipe the entire workspace tree on disk, the sources' mirrors included.

    Removes ``<data_dir>/workspace/`` and ``<data_dir>/sources/`` and
    re-creates the empty workspace root.  Used by the admin "reset workspace
    files" action; the caller is responsible for clearing the matching SQL
    documents (cascade then drops the vector rows) and the sources' sync
    state, since this is a filesystem-only operation.
    """
    workspace_root = Workspace.workspace_root()
    await asyncio.to_thread(remove_path, workspace_root)
    await asyncio.to_thread(remove_path, settings.data_dir / "sources")
    workspace_root.mkdir(parents=True, exist_ok=True)
