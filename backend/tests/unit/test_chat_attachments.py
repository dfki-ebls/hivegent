"""Unit tests for the chat composer's attachment gate."""

import io

import pytest
from exar.ingest import MAX_DIMENSION
from fastapi import HTTPException
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from hivegent.config import settings
from hivegent.llm_config import LlmConfig
from hivegent.server.routes.conversations.attachments import accept_attachments
from tests.helpers import png_bytes

_SEEING = LlmConfig(model="m", vision=True)


def _request(*content: str | BinaryContent) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(content=list(content))])]


async def test_image_is_admitted_and_sanitized_in_place() -> None:
    """The rewrite lands on the message the adapter caches, past the prompt text."""
    info = PngInfo()
    info.add_text("Comment", "x" * 4096)

    messages = _request(
        "look at this", BinaryContent(data=png_bytes(info), media_type="image/png")
    )
    await accept_attachments(messages, llm=_SEEING)

    part = messages[0].parts[0]
    assert isinstance(part, UserPromptPart)
    assert isinstance(part.content, list)
    attached = part.content[1]
    assert isinstance(attached, BinaryContent)
    assert b"Comment" not in attached.data


async def test_a_large_still_is_downscaled() -> None:
    buffer = io.BytesIO()
    Image.effect_noise((3000, 1500), 64).convert("RGB").save(buffer, format="JPEG")
    attached = BinaryContent(data=buffer.getvalue(), media_type="image/jpeg")

    await accept_attachments(_request(attached), llm=_SEEING)

    assert attached.media_type == "image/jpeg"
    assert Image.open(io.BytesIO(attached.data)).size == (
        MAX_DIMENSION,
        MAX_DIMENSION // 2,
    )


@pytest.mark.parametrize("media_type", ["application/pdf", "image/svg+xml"])
async def test_non_ingestible_type_is_rejected(media_type: str) -> None:
    """A document, and an image no vision backend ingests, are both refused."""
    with pytest.raises(HTTPException) as exc:
        await accept_attachments(
            _request(BinaryContent(data=b"x", media_type=media_type)), llm=_SEEING
        )

    assert exc.value.status_code == 400
    assert "workspace" in str(exc.value.detail)


async def test_oversized_image_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is checked before the bytes are parsed as an image."""
    monkeypatch.setattr(settings.limits, "max_attachment_bytes", 16)

    with pytest.raises(HTTPException) as exc:
        await accept_attachments(
            _request(BinaryContent(data=b"x" * 32, media_type="image/png")),
            llm=_SEEING,
        )

    assert exc.value.status_code == 400


async def test_more_images_than_the_gateway_takes_are_rejected() -> None:
    """The count is the model's gateway cap, refused here rather than mid-stream.

    A request over the per-request image cap is rejected whole by the serving
    gateway, so the turn would fail once it was already running.
    """
    capped = LlmConfig(model="m", vision=True, max_images=1)
    png = png_bytes()

    await accept_attachments(
        _request(BinaryContent(data=png, media_type="image/png")), llm=capped
    )

    with pytest.raises(HTTPException) as exc:
        await accept_attachments(
            _request(
                BinaryContent(data=png, media_type="image/png"),
                BinaryContent(data=png, media_type="image/png"),
            ),
            llm=capped,
        )

    assert exc.value.status_code == 400
    assert "At most 1 image" in str(exc.value.detail)


async def test_a_model_without_vision_takes_no_images() -> None:
    """The catalog's `vision` flag is the gate, not the composer hiding its button."""
    with pytest.raises(HTTPException) as exc:
        await accept_attachments(
            _request(BinaryContent(data=png_bytes(), media_type="image/png")),
            llm=LlmConfig(model="m"),
        )

    assert exc.value.status_code == 400
