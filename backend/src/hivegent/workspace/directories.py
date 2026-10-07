"""Store-wide wipes.

Creating, moving, and deleting a directory are changeset items
(:mod:`~hivegent.workspace.changeset`), so they commit atomically with whatever
else a change does.  The store-wide wipe takes the lock with ``whole_store`` so
it cannot race a phased upload or a bulk import.
"""

import asyncio

from ..config import settings
from ..db import documents as db_documents
from ..files import remove_path
from ..store import Casebase
from .locks import _locked_for

__all__ = [
    "delete_all",
    "delete_workspace_root",
]


async def delete_all(store: Casebase) -> None:
    """Wipe every trace of a casebase: workspace files + SQL rows.

    Chunks cascade-delete with the documents.
    """
    async with _locked_for(store, whole_store=True):
        await db_documents.delete_all(store)
        await asyncio.to_thread(remove_path, store.workspace_path(settings.data_dir))


async def delete_workspace_root() -> None:
    """Wipe the entire workspace tree on disk.

    Removes ``<data_dir>/workspace/`` and re-creates the empty root.
    Used by the admin "reset workspace files" action; the caller is
    responsible for clearing the matching SQL documents (cascade then
    drops the vector rows), since this is a filesystem-only operation.
    """
    workspace_root = Casebase.workspace_root(settings.data_dir)
    await asyncio.to_thread(remove_path, workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
