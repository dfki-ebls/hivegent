"""Route for speech-to-text transcription of recorded audio."""

import logging

from fastapi import APIRouter, HTTPException, UploadFile
from starlette.requests import Request

from ...auth import CurrentUser
from ...config import settings
from ...llm import create_openai_client
from ...llm_config import stt_llm
from ...shared.l10n import Localized, format_bytes
from ...types import TranscriptionResponse
from ..cancellation import run_until_disconnect
from ..operations import enforce_upload_size, upload_body_limit
from ..rate_limit import TRANSCRIPTION_LIMIT

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()

_NO_MODEL = Localized(
    en="No transcription model configured",
    de="Kein Transkriptionsmodell konfiguriert",
)
_FAILED = Localized(
    en="Failed to transcribe audio", de="Audio konnte nicht transkribiert werden"
)


def _too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"Recording too large. Maximum size: {format_bytes(limit)}",
        de=f"Aufnahme zu groß. Maximale Größe: {format_bytes(limit)}",
    )


@router.post(
    "/transcription",
    dependencies=[TRANSCRIPTION_LIMIT],
    max_body_size=upload_body_limit(settings.limits.max_file_size_bytes),
)
async def create_transcription(
    audio: UploadFile,
    http_request: Request,
    _user: CurrentUser,
) -> TranscriptionResponse:
    """Transcribe recorded audio with the configured STT model.

    Backs the speech input fallback for browsers without a working Web
    Speech API (Firefox, Safari, Vivaldi): they record locally and send
    the audio here.  Responds 501 when no STT model is configured.
    """
    stt = stt_llm()
    if stt is None:
        raise HTTPException(status_code=501, detail=_NO_MODEL.current)

    enforce_upload_size(
        audio, limit=settings.limits.max_file_size_bytes, too_large=_too_large
    )

    client = create_openai_client(stt.provider)

    async def _transcribe() -> str:
        result = await client.audio.transcriptions.create(
            model=stt.model,
            file=(
                audio.filename or "audio.webm",
                await audio.read(),
                audio.content_type or "audio/webm",
            ),
        )
        return result.text

    try:
        text = await run_until_disconnect(http_request, _transcribe())
    except Exception as exc:
        logger.exception("Failed to transcribe audio")
        raise HTTPException(status_code=500, detail=_FAILED.current) from exc
    return TranscriptionResponse(text=text)
