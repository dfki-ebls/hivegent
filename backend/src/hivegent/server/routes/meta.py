"""Routes for settings, tool metadata, and pipeline metadata."""

from fastapi import APIRouter

from ...auth import CurrentUser
from ...config import settings
from ...shared.formats import INGESTIBLE_IMAGE_MEDIA_TYPES
from ...transparency import get_transparency_config
from ...types import (
    AttachmentLimits,
    ModelInfo,
    SettingsResponse,
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
