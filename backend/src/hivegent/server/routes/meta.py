"""Routes for settings, tool metadata, and pipeline metadata."""

from collections.abc import Sequence

from fastapi import APIRouter

from ...agents import tool_infos
from ...agents.integrations import integration_tool_infos
from ...auth import CurrentUser
from ...config import settings
from ...shared.formats import INGESTIBLE_IMAGE_MEDIA_TYPES
from ...transparency import get_transparency_config
from ...types import (
    AttachmentLimits,
    ModelInfo,
    SettingsResponse,
    ToolInfo,
    UserResponse,
)

__all__ = ["router"]

router = APIRouter()

@router.get("/settings")
async def get_settings(
    user: CurrentUser,
) -> SettingsResponse:
    """Get what the server offers the user, and their own metadata."""
    return SettingsResponse(
        models=ModelInfo.catalog(),
        default_model=settings.llm.default,
        transcription=settings.llm.stt is not None,
        user=UserResponse.from_user(user),
        attachments=AttachmentLimits(
            media_types=sorted(INGESTIBLE_IMAGE_MEDIA_TYPES),
            max_bytes=settings.limits.max_attachment_bytes,
        ),
        transparency=get_transparency_config(),
        ragold_url=None if settings.ragold.url is None else str(settings.ragold.url),
        debug=settings.debug.enable and user.is_admin,
    )


@router.get("/tools")
async def list_tools(
    user: CurrentUser,
) -> Sequence[ToolInfo]:
    """Return the tools and subagents a chat model may be offered, the switchable ones marked.

    The catalog is :func:`tool_infos`, whose ``switchable`` entries are what
    the settings dialog renders a checkbox per and what ``disabled_tools``
    takes.  The tools of the external servers, databases, and APIs *user* sees
    follow, each source's under its id, as long as it is connected, unless the
    operator withholds them.
    """
    disabled = set(settings.tools.disabled)

    return [
        *tool_infos(),
        *(tool for tool in integration_tool_infos(user) if tool.name not in disabled),
    ]
