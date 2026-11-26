"""Routes for the user's preferences, memory, export, and user-wide cleanup."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from starlette.responses import StreamingResponse

from ...auth import CurrentUser
from ...config import settings
from ...db.conversations import owned_conversations
from ...db.memory import clear_memory, load_memory, memory_fits, save_memory
from ...db.users import update_preferences
from ...shared.l10n import Localized
from ...tmp import clear_tmp
from ...types import MemoryPage, Preferences, TmpClearedResponse
from ..common import delete_user_data, user_preferences
from ..export import ExportPart, build_export, export_filename
from ..operations import zip_response
from ..rate_limit import GENERATION_LIMIT

__all__ = ["router"]

router = APIRouter()


@router.get("/preferences")
async def get_preferences(user: CurrentUser) -> Preferences:
    """The choices the user made for their runs, each one never made or no longer valid at its default."""
    return await user_preferences(user)


@router.patch("/preferences")
async def patch_preferences(changes: Preferences, user: CurrentUser) -> Preferences:
    """Store the choices *changes* sets, keeping the others, so clients changing different ones never undo each other."""
    stored = await update_preferences(
        user.id, changes.model_dump(mode="json", exclude_unset=True)
    )

    return Preferences.stored(stored)


@router.get("/export", dependencies=[GENERATION_LIMIT])
async def export_user_data(
    user: CurrentUser,
    part: Annotated[frozenset[ExportPart], Query()] = frozenset(ExportPart),
) -> StreamingResponse:
    """Download each *part* of the caller's data, every one by default, as one ZIP archive (:func:`build_export`).

    The archive is complete before the first byte is sent, so a failure is
    an HTTP error, never a truncated archive.
    """
    return zip_response(await build_export(user, part), export_filename(part))


@router.get("/memory")
async def get_memory(user: CurrentUser) -> MemoryPage:
    """The authenticated user's memory page, empty while they keep none."""
    return MemoryPage(content=await load_memory(user) or "")


@router.put("/memory")
async def put_memory(page: MemoryPage, user: CurrentUser) -> MemoryPage:
    """Replace the authenticated user's memory page, an empty one clearing it.

    An oversized page may only shrink, so a lowered limit never locks it.
    """
    limit = settings.limits.max_memory_chars

    if not memory_fits(page.content, await load_memory(user) or "", limit):
        detail = Localized(
            en=f"The memory holds at most {limit} characters",
            de=f"Das Gedächtnis fasst höchstens {limit} Zeichen",
        )
        raise HTTPException(status_code=422, detail=detail.current)

    await save_memory(user, page.content)

    return page


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
