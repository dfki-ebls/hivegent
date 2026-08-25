"""Routes for directory management.

Like the document routes, these take a canonical workspace path:
``~/<local>`` for the caller's personal store, or ``@<group>/<local>``
for a group. The tree endpoint takes the bare scope segment (``~`` or
``@<group>``) in the URL path.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ... import workspace
from ...auth import User, get_current_user
from ...types import (
    CreateDirectoryRequest,
    DeleteDirectoryRequest,
    DirectoryTreeResponse,
    MoveDirectoryRequest,
)
from ...workspace_events import notify_workspace_change
from ..common import ClientId, resolve_move, resolve_workspace_path
from ..operations import build_tree_response

__all__ = ["router"]

router = APIRouter()


@router.get("/directories/{scope}")
async def get_directories(
    scope: str,
    user: Annotated[User, Depends(get_current_user)],
) -> DirectoryTreeResponse:
    """Build a recursive directory tree for a workspace (``~`` or ``@<group>``)."""
    store, _ = resolve_workspace_path(user, scope)
    return await build_tree_response(store)


@router.post("/directories", status_code=status.HTTP_204_NO_CONTENT)
async def create_directory(
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> None:
    """Create a new directory within a workspace."""
    store, safe = resolve_workspace_path(user, request.path, write=True)
    await workspace.create_directory(store, safe)
    notify_workspace_change(user.id, store, client)


@router.post("/directories/move", status_code=status.HTTP_204_NO_CONTENT)
async def move_directory(
    request: MoveDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> None:
    """Move/rename a directory within a workspace or migrate it to another.

    Resolving both ends with ``write=True`` requires write access to each, so a
    cross-workspace directory move is allowed exactly when the caller may write
    both the source and the destination.
    """
    src_store, safe_src, dst_store, safe_dst = resolve_move(
        user, request.source, request.destination
    )
    await workspace.move_directory(src_store, dst_store, safe_src, safe_dst)
    # Both ends change on a cross-workspace move; the same store twice collapses
    # to one notification for an in-place one.
    notify_workspace_change(user.id, src_store, client)
    if dst_store != src_store:
        notify_workspace_change(user.id, dst_store, client)


@router.delete("/directories", status_code=status.HTTP_204_NO_CONTENT)
async def delete_directory(
    request: DeleteDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> None:
    """Delete a directory and all of its contents."""
    store, safe = resolve_workspace_path(user, request.path, write=True)
    await workspace.delete_directory(store, safe)
    notify_workspace_change(user.id, store, client)
