"""Routes for user directory management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...auth import User, get_current_user
from ...config import settings
from ...retrieval import mark_dirty_and_sync
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
from ..operations import (
    build_tree_response,
    delete_directory_internal,
    move_directory_internal,
)

__all__ = ["router"]

router = APIRouter()


@router.get("/directories")
async def get_directories(
    user: Annotated[User, Depends(get_current_user)],
) -> DirectoryTreeResponse:
    """Build a recursive directory tree from the user's documents directory."""
    return build_tree_response(user_store(user))


@router.post("/directories")
async def create_directory(
    request: CreateDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CreateDirectoryResponse:
    """Create a new directory within the user's documents directory."""
    safe = safe_path(request.path)
    store = user_store(user)
    directory_path = store.workspace_dir(settings.data_dir) / safe
    if directory_path.exists():
        raise HTTPException(status_code=409, detail="Directory already exists")
    directory_path.mkdir(parents=True, exist_ok=True)
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
    return move_directory_internal(user_store(user), safe_src, safe_dst)


@router.delete("/directories")
async def delete_directory(
    request: DeleteDirectoryRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> DeleteDirectoryResponse:
    """Delete a directory and all of its contents."""
    safe = safe_path(request.path)
    store = user_store(user)
    files_deleted = delete_directory_internal(store, safe)
    mark_dirty_and_sync(store)
    return DeleteDirectoryResponse(
        path=safe,
        files_deleted=files_deleted,
        message="Directory deleted successfully",
    )
