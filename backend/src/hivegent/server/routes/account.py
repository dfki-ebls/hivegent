"""Routes for memory and user-wide cleanup."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ... import workspace
from ...auth import User, get_current_user
from ...db.memory import clear_memory
from ...db.users import delete_user
from ...types import BulkDeleteUserDataResponse, ClearMemoryResponse
from ..common import user_store

__all__ = ["router"]

router = APIRouter()


@router.delete("/memory")
async def delete_memory(
    user: Annotated[User, Depends(get_current_user)],
) -> ClearMemoryResponse:
    """Clear the authenticated user's persistent memory."""
    cleared = await clear_memory(user.id)
    return ClearMemoryResponse(
        cleared=cleared,
        message="Memory cleared" if cleared else "No memory to clear",
    )


@router.delete("/user-data")
async def delete_all_user_data(
    user: Annotated[User, Depends(get_current_user)],
) -> BulkDeleteUserDataResponse:
    """Delete all data for the authenticated user.

    ``workspace.delete_all`` clears documents (cascades to chunks via
    FK ``ON DELETE CASCADE``) and the workspace directory.
    ``delete_user`` then drops the ``User`` row so memory and
    conversations cascade.  The user re-materialises lazily on the next
    request via :func:`ensure_user`.
    """
    store = user_store(user)
    await workspace.delete_all(store)
    await delete_user(user.id)
    return BulkDeleteUserDataResponse(
        message="All user data deleted successfully",
    )
