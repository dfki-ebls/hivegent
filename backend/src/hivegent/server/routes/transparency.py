"""Authenticated AI-generated text watermark verification routes.

The key set that verifies their reports is public, see :mod:`.public`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ...config import TransparencySettings, settings
from ...l10n import Localized
from ...transparency import TransparencyUnavailable, detect_text
from ...types import (
    TransparencyDetectionRequest,
    TransparencyDetectionResponse,
)

__all__ = ["TransparencyConfigured", "router"]

_DISABLED = Localized(en="Transparency is disabled", de="Transparenz ist deaktiviert")
_UNAVAILABLE = Localized(
    en="Watermark detection is temporarily unavailable",
    de="Die Wasserzeichenerkennung ist vorübergehend nicht verfügbar",
)


def _require_transparency() -> TransparencySettings:
    """Hide every transparency route while the feature is not configured."""
    if settings.transparency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _DISABLED.current)

    return settings.transparency


TransparencyConfigured = Annotated[TransparencySettings, Depends(_require_transparency)]

router = APIRouter(prefix="/transparency")


@router.post("/detect")
async def detect_watermark(
    request: TransparencyDetectionRequest,
    config: TransparencyConfigured,
) -> TransparencyDetectionResponse:
    """Verify a text watermark without logging or retaining submitted content."""
    try:
        return await detect_text(config, request.text)
    except TransparencyUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            _UNAVAILABLE.current,
        ) from exc
