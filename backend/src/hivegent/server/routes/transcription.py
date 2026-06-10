"""Route for speech-to-text transcription of recorded audio."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from starlette.requests import Request

from ...auth import User, get_current_user
from ...config import settings
from ...llm import create_openai_client
from ...types import TranscriptionResponse
from ..cancellation import run_until_disconnect

__all__ = ["router"]

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/transcription")
async def create_transcription(
    audio: UploadFile,
    http_request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> TranscriptionResponse:
    """Transcribe recorded audio with the configured STT model.

    Backs the speech input fallback for browsers without a working Web
    Speech API (Firefox, Safari, Vivaldi): they record locally and send
    the audio here.  Responds 501 when no STT model is configured.
    """
    stt_model = settings.llm.stt_model
    if not stt_model:
        raise HTTPException(status_code=501, detail="No transcription model configured")

    # Server-configured credentials only; the base URL is trusted
    # operator input (may legitimately point at a private host).
    client = create_openai_client(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url or None,
        allow_private_base_url=True,
    )

    async def _transcribe() -> str:
        result = await client.audio.transcriptions.create(
            model=stt_model,
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
        raise HTTPException(
            status_code=500, detail="Failed to transcribe audio"
        ) from exc
    return TranscriptionResponse(text=text)
