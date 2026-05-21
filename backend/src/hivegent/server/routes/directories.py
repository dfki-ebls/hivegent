"""Routes for user directory management."""

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
from ..common import safe_path, user_store
from ..operations import build_tree_response

__all__ = ["router"]

router = APIRouter()


@router.get("/directories")
async def get_directories(
    user: Annotated[User, Depends(get_current_user)],
) -> DirectoryTreeResponse:
    """Build a recursive directory tree from the user's documents directory."""
    return await build_tree_response(user_store(user))


@router.post("/directories")
async def create_directory(
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateDirectoryResponse:
    """Create a new directory within the user's documents directory."""
    safe = safe_path(request.path)
    await workspace.create_directory(user_store(user), safe)
    return CreateDirectoryResponse(
        path=safe,
        message="Directory created successfully",
    )


@router.post("/directories/move")
async def move_directory(
    request: MoveDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> MoveDirectoryResponse:
    """Move/rename a directory within the user's documents directory."""
    safe_src = safe_path(request.source)
    safe_dst = safe_path(request.destination)
    return await workspace.move_directory(user_store(user), safe_src, safe_dst)


@router.delete("/directories")
async def delete_directory(
    request: DeleteDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDirectoryResponse:
    """Delete a directory and all of its contents."""
    safe = safe_path(request.path)
    files_deleted = await workspace.delete_directory(user_store(user), safe)
    return DeleteDirectoryResponse(
        path=safe,
        files_deleted=files_deleted,
        message="Directory deleted successfully",
    )
