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


def _png() -> BinaryContent:
    return BinaryContent(data=png_bytes(), media_type="image/png")


@pytest.mark.parametrize(
    ("content", "llm", "detail"),
    [
        (BinaryContent(b"x", media_type="application/pdf"), _SEEING, "workspace"),
        (BinaryContent(b"x", media_type="image/svg+xml"), _SEEING, "workspace"),
        (_png(), LlmConfig(model="m"), "does not accept images"),
    ],
    ids=["document", "image-no-backend-ingests", "model-without-vision"],
)
async def test_an_attachment_the_model_cannot_take_is_rejected(
    content: BinaryContent, llm: LlmConfig, detail: str
) -> None:
    """Only ingestible images are taken, and only by a model whose catalog entry says `vision`, not by the composer hiding its button."""
    with pytest.raises(HTTPException) as exc:
        await accept_attachments(_request(content), llm=llm)

    assert exc.value.status_code == 400
    assert detail in str(exc.value.detail)


async def test_oversized_image_is_rejected() -> None:
    """The cap is checked before the bytes are parsed as an image."""
    oversized = b"x" * (settings.limits.max_attachment_bytes + 1)

    with pytest.raises(HTTPException) as exc:
        await accept_attachments(
            _request(BinaryContent(data=oversized, media_type="image/png")),
            llm=_SEEING,
        )

    assert exc.value.status_code == 400


async def test_more_images_than_the_gateway_takes_are_rejected() -> None:
    """The count is the gateway's cap, refused here rather than once the turn is running."""
    capped = LlmConfig(model="m", vision=True, max_images=1)

    await accept_attachments(_request(_png()), llm=capped)

    with pytest.raises(HTTPException) as exc:
        await accept_attachments(_request(_png(), _png()), llm=capped)

    assert exc.value.status_code == 400
    assert "At most 1 image" in str(exc.value.detail)
