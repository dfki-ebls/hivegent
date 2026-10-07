"""Routes for memory and user-wide cleanup."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ... import workspace
from ...auth import User, get_current_user
from ...db.conversations import conversation_ids
from ...db.memory import clear_memory
from ...db.users import delete_user
from ...tmp import clear_tmp, remove_tmp
from ...types import TmpClearedResponse
from ...workspace_events import notify_workspace_change
from ..common import ClientId, user_store

__all__ = ["router"]

router = APIRouter()


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Clear the authenticated user's persistent memory."""
    await clear_memory(user.id)


@router.delete("/tmp")
async def delete_tmp(
    user: Annotated[User, Depends(get_current_user)],
) -> TmpClearedResponse:
    """Drop the ``/tmp`` folder of every one of the caller's conversations.

    The folders live outside every workspace, so the file count is the whole
    of the result and no client refresh follows.
    """
    removed = await clear_tmp(await conversation_ids(user.id))

    return TmpClearedResponse(files_removed=removed)


@router.delete("/user-data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_user_data(
    user: Annotated[User, Depends(get_current_user)],
    client: ClientId = None,
) -> None:
    """Delete all data for the authenticated user.

    ``workspace.delete_all`` clears documents (cascades to chunks via
    FK ``ON DELETE CASCADE``) and the workspace directory.
    ``delete_user`` then drops the ``User`` row so memory and
    conversations cascade, after their ``/tmp`` folders went, which the
    cascade cannot reach.  The user re-materialises lazily on the next
    request via :func:`ensure_user`.
    """
    store = user_store(user)
    await workspace.delete_all(store)
    await remove_tmp(await conversation_ids(user.id))
    await delete_user(user.id)
    notify_workspace_change(user.id, store, client)
