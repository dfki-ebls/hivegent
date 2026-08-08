"""Directory create/move/delete, pruning, and store-wide wipes.

Operate on whole subtrees of the workspace; document rows follow their files
via the SQL subtree helpers.  The destructive ops take the lock with a
``scope`` (or ``whole_store``) so they cannot race a phased upload or a bulk
import that touches the same subtree.
"""

import asyncio
from collections.abc import Iterable
from pathlib import PurePosixPath

from fastapi import HTTPException

from ..concurrency import shield_to_completion
from ..config import settings
from ..db import documents as db_documents
from ..store import Casebase
from ..types import MoveDirectoryResponse
from .locks import _locked_for, _locked_for_move, store_lock
from .paths import (
    _check_destination_parents,
    _check_not_assets_path,
    _count_files,
    _is_blocked_by_other,
    _is_same_file,
    _remove_tree,
    _resolve_move_destination,
)

__all__ = [
    "create_directory",
    "delete_all",
    "delete_directory",
    "delete_workspace_root",
    "move_directory",
    "prune_empty_dirs",
]


async def create_directory(store: Casebase, path: str) -> None:
    """Create an empty workspace directory."""
    if not path:
        raise HTTPException(status_code=400, detail="Directory path required")
    _check_not_assets_path(path)
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        directory_path = workspace_dir / path
        if directory_path.exists():
            raise HTTPException(status_code=409, detail="Path already exists")
        _check_destination_parents(workspace_dir, path)
        directory_path.mkdir(parents=True, exist_ok=True)


async def _move_directory_locked(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> MoveDirectoryResponse:
    """Move a directory's files and SQL rows. Caller holds the lock(s).

    Source paths resolve under *src_store*'s workspace and destination paths
    under *dst_store*'s, so this renames a subtree within one workspace or
    migrates it to another (personal ↔ group, group ↔ group).
    """
    src_workspace = src_store.workspace_dir(settings.data_dir)
    # Non-creating: the rename step below makes the destination tree, so
    # validation never needs the directory to exist and a rejected move leaves
    # no empty workspace behind.
    dst_workspace = dst_store.workspace_path(settings.data_dir)
    cross_store = src_store != dst_store

    if not src:
        raise HTTPException(status_code=400, detail="Directory path required")
    _check_not_assets_path(src)
    src_dir = src_workspace / src
    if not src_dir.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dst = _resolve_move_destination(
        dst_workspace, PurePosixPath(src).name, dst, src_dir
    )
    _check_not_assets_path(dst)
    if not cross_store and dst == src:
        raise HTTPException(
            status_code=400, detail="Source and destination are the same"
        )
    dst_dir = dst_workspace / dst
    # Reject moving a directory beneath itself.  Inode comparison against the
    # destination's existing ancestors also catches case-aliased spellings on
    # a case-insensitive filesystem, where a string prefix check would not.  A
    # cross-store destination is a different tree, so no ancestor can alias the
    # source and the loop is a no-op there.
    for ancestor in dst_dir.parents:
        if ancestor == dst_workspace:
            break
        if _is_same_file(ancestor, src_dir):
            raise HTTPException(
                status_code=400, detail="Cannot move a directory into itself"
            )
    _check_destination_parents(dst_workspace, dst)
    if _is_blocked_by_other(dst_dir, src_dir):
        raise HTTPException(status_code=409, detail="Destination already exists")

    files_moved = await asyncio.to_thread(_count_files, src_dir)
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    src_dir.rename(dst_dir)

    # Children-only: a same-named sibling document (stem equal to ``src``)
    # lives outside the directory and keeps its row.
    await db_documents.move_subtree(src_store, src, dst_store, dst)

    return MoveDirectoryResponse(
        source=src,
        destination=dst,
        files_moved=files_moved,
        message="Directory moved successfully",
    )


async def prune_empty_dirs(store: Casebase, sources: Iterable[str]) -> None:
    """Remove directories left empty after their entries moved away.

    *sources* are the workspace-relative paths of moved entries; every
    ancestor directory of each is a candidate.  Removal uses non-recursive
    ``rmdir`` deepest-first, so a directory still holding anything — even
    content invisible to the directory tree — survives untouched.
    """
    candidates = {
        str(ancestor)
        for source in sources
        for ancestor in PurePosixPath(source).parents
        if str(ancestor) != "."
    }
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        for rel in sorted(candidates, key=lambda p: p.count("/"), reverse=True):
            try:
                (workspace_dir / rel).rmdir()
            except OSError:
                continue


async def move_directory(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> MoveDirectoryResponse:
    """Move or rename a workspace directory; document rows follow via SQL.

    *src_store* and *dst_store* may be the same casebase (a rename within one
    workspace) or two different ones (migrating a folder between the personal
    and a shared workspace).  The FS move + SQL move run to completion under the
    lock(s) even on a cancel (:func:`shield_to_completion`) so the directory and
    its rows cannot drift apart.
    """
    async with _locked_for_move(src_store, dst_store, scope=src):
        return await shield_to_completion(
            _move_directory_locked(src_store, dst_store, src, dst)
        )


async def _delete_directory_locked(store: Casebase, path: str) -> int:
    """Delete a directory's files and SQL rows. Caller holds the lock."""
    if not path:
        # A bare scope root resolves to an empty path; deleting it here would
        # wipe the workspace files while leaving every SQL row behind.  The
        # full wipe (files + rows) is `delete_all`.
        raise HTTPException(status_code=400, detail="Directory path required")
    workspace_dir = store.workspace_dir(settings.data_dir)
    directory_path = workspace_dir / path
    if not directory_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    files_deleted = await asyncio.to_thread(_count_files, directory_path)
    await asyncio.to_thread(_remove_tree, directory_path)
    # Children-only: a same-named sibling document (stem equal to *path*)
    # lives outside the directory and keeps its row.
    await db_documents.delete_subtree(store, path)
    return files_deleted


async def delete_directory(store: Casebase, path: str) -> int:
    """Delete a workspace directory; matching SQL documents cascade out.

    The FS removal + SQL delete run to completion under the lock even on a
    cancel (:func:`shield_to_completion`) so the directory cannot vanish while
    its rows linger.
    """
    async with _locked_for(store, scope=path):
        return await shield_to_completion(_delete_directory_locked(store, path))


async def delete_all(store: Casebase) -> None:
    """Wipe every trace of a casebase: workspace files + SQL rows.

    Chunks cascade-delete with the documents.
    """
    async with _locked_for(store, whole_store=True):
        await db_documents.delete_all(store)
        await asyncio.to_thread(_remove_tree, store.workspace_path(settings.data_dir))


async def delete_workspace_root() -> None:
    """Wipe the entire workspace tree on disk.

    Removes ``<data_dir>/workspace/`` and re-creates the empty root.
    Used by the admin "reset workspace files" action; the caller is
    responsible for clearing the matching SQL documents (cascade then
    drops the vector rows), since this is a filesystem-only operation.
    """
    workspace_root = Casebase.workspace_root(settings.data_dir)
    await asyncio.to_thread(_remove_tree, workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
