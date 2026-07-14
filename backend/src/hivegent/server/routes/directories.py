"""Routes for directory management.

Like the document routes, these take a canonical workspace path:
``~/<local>`` for the caller's personal store, or ``@<group>/<local>``
for a group. The tree endpoint takes the bare scope segment (``~`` or
``@<group>``) in the URL path.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from ... import workspace
from ...auth import User, get_current_user
from ...types import (
    CreateDirectoryRequest,
    CreateDirectoryResponse,
    DeleteDirectoryRequest,
    DeleteDirectoryResponse,
    DirectoryTreeResponse,
    MoveDirectoryRequest,
    MoveDirectoryResponse,
)
from ..common import resolve_move, resolve_workspace_path
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


@router.post("/directories")
async def create_directory(
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateDirectoryResponse:
    """Create a new directory within a workspace."""
    store, safe = resolve_workspace_path(user, request.path, write=True)
    await workspace.create_directory(store, safe)
    return CreateDirectoryResponse(
        path=safe,
        message="Directory created successfully",
    )


@router.post("/directories/move")
async def move_directory(
    request: MoveDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDirectoryResponse:
    """Move/rename a directory within a workspace or migrate it to another.

    Resolving both ends with ``write=True`` requires write access to each, so a
    cross-workspace directory move is allowed exactly when the caller may write
    both the source and the destination.
    """
    src_store, safe_src, dst_store, safe_dst = resolve_move(
        user, request.source, request.destination
    )
    return await workspace.move_directory(src_store, dst_store, safe_src, safe_dst)


@router.delete("/directories")
async def delete_directory(
    request: DeleteDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDirectoryResponse:
    """Delete a directory and all of its contents."""
    store, safe = resolve_workspace_path(user, request.path, write=True)
    files_deleted = await workspace.delete_directory(store, safe)
    return DeleteDirectoryResponse(
        path=safe,
        files_deleted=files_deleted,
        message="Directory deleted successfully",
    )
