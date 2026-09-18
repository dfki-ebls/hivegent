"""Authenticated AI-generated text watermark verification routes.

The key set is authenticated like the detection endpoint itself. It carries
only a public verification key, so exposing it would be harmless, but a report
is only ever handed to someone who already has access, and an unauthenticated
route is attack surface bought for nobody.
"""

from fastapi import APIRouter, HTTPException, status

from ...config import settings
from ...transparency import TransparencyUnavailable, detect_text, public_jwks
from ...types import (
    TransparencyDetectionRequest,
    TransparencyDetectionResponse,
    TransparencyJwks,
)

__all__ = ["router"]

router = APIRouter(prefix="/transparency")


def _require_enabled() -> None:
    if not settings.transparency.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Watermark detection is disabled")


@router.post("/detect")
async def detect_watermark(
    request: TransparencyDetectionRequest,
) -> TransparencyDetectionResponse:
    """Verify a text watermark without logging or retaining submitted content."""
    _require_enabled()

    try:
        return await detect_text(request.text)
    except TransparencyUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Watermark detection is temporarily unavailable",
        ) from exc


@router.get("/jwks")
async def transparency_jwks() -> TransparencyJwks:
    """Publish the key used to verify signed watermark detection reports."""
    _require_enabled()

    return public_jwks()
