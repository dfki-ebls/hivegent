"""Routes for the user's preferences, memory, and user-wide cleanup."""

from fastapi import APIRouter, HTTPException, status

from ...assistants import resolve_assistant
from ...auth import CurrentUser
from ...db.conversations import owned_conversations
from ...db.memory import clear_memory
from ...db.users import update_preferences
from ...shared.l10n import Localized
from ...tmp import clear_tmp
from ...types import Preferences, TmpClearedResponse
from ..common import delete_user_data, effective_preferences

__all__ = ["router"]

router = APIRouter()


def _unknown_assistant(name: str) -> Localized[str]:
    return Localized(
        en=f"No assistant named {name!r} is available",
        de=f"Es gibt keinen verfügbaren Assistenten „{name}“",
    )


@router.get("/preferences")
async def get_preferences(user: CurrentUser) -> Preferences:
    """The choices the user made for their runs, each one never made or no longer valid at its default."""
    return (await effective_preferences(user)).preferences


@router.patch("/preferences")
async def patch_preferences(changes: Preferences, user: CurrentUser) -> Preferences:
    """Store the choices *changes* sets, keeping the others, so clients changing different ones never undo each other."""
    if changes.assistant is not None and await resolve_assistant(user, changes.assistant) is None:
        raise HTTPException(
            status_code=422, detail=_unknown_assistant(changes.assistant).current
        )

    stored = await update_preferences(
        user.id, changes.model_dump(mode="json", exclude_unset=True)
    )

    return (await effective_preferences(user, stored)).preferences


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
) -> None:
    """Delete all data for the authenticated user (:func:`delete_user_data`)."""
    await delete_user_data(user)
