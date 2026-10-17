"""Routes for memory and user-wide cleanup."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...auth import get_current_user
from ...db.conversations import owned_conversations
from ...db.memory import clear_memory
from ...store import User
from ...tmp import clear_tmp
from ...types import TmpClearedResponse
from ..common import ClientId, delete_user_data

__all__ = ["router"]

router = APIRouter()


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Clear the authenticated user's persistent memory."""
    await clear_memory(user)


@router.delete("/tmp")
async def delete_tmp(
    user: Annotated[User, Depends(get_current_user)],
) -> TmpClearedResponse:
    """Drop the ``/tmp`` folder of every one of the caller's conversations.

    The folders live outside every workspace, so the file count is the whole
    of the result and no client refresh follows.
    """
    removed = await clear_tmp(await owned_conversations(user))

    return TmpClearedResponse(files_removed=removed)


@router.delete("/user-data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_user_data(
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> None:
    """Delete all data for the authenticated user (:func:`delete_user_data`)."""
    await delete_user_data(user, exclude_client=client)
