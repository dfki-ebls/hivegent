"""Routes for memory and user-wide cleanup."""

from fastapi import APIRouter, status

from ...auth import CurrentUser
from ...db.conversations import owned_conversations
from ...db.memory import clear_memory
from ...tmp import clear_tmp
from ...types import TmpClearedResponse
from ..common import ClientId, delete_user_data

__all__ = ["router"]

router = APIRouter()


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    user: CurrentUser,
) -> None:
    """Clear the authenticated user's persistent memory."""
    await clear_memory(user)


@router.delete("/tmp")
async def delete_tmp(
    user: CurrentUser,
) -> TmpClearedResponse:
    """Drop the ``/tmp`` folder of every one of the caller's conversations.

    The folders live outside every workspace, so the file count is the whole
    of the result and no client refresh follows.
    """
    removed = await clear_tmp(await owned_conversations(user))

    return TmpClearedResponse(files_removed=removed)


@router.delete("/user-data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_user_data(
    user: CurrentUser,
    client: ClientId = None,
) -> None:
    """Delete all data for the authenticated user (:func:`delete_user_data`)."""
    await delete_user_data(user, exclude_client=client)
