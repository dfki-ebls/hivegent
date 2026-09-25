"""Authenticated AI-generated text watermark verification routes.

The key set that verifies their reports is public, see :mod:`.public`.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from ...config import settings
from ...transparency import TransparencyUnavailable, detect_text
from ...types import (
    TransparencyDetectionRequest,
    TransparencyDetectionResponse,
)

__all__ = ["require_transparency", "router"]


def require_transparency() -> None:
    """Hide every transparency route while the feature is disabled."""
    if not settings.transparency.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transparency is disabled")


router = APIRouter(prefix="/transparency", dependencies=[Depends(require_transparency)])


@router.post("/detect")
async def detect_watermark(
    request: TransparencyDetectionRequest,
) -> TransparencyDetectionResponse:
    """Verify a text watermark without logging or retaining submitted content."""
    try:
        return await detect_text(request.text)
    except TransparencyUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Watermark detection is temporarily unavailable",
        ) from exc
