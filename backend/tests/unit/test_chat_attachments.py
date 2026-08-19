"""Unit tests for the chat composer's attachment gate."""

import io

import pytest
from fastapi import HTTPException
from PIL import Image, PngImagePlugin
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from hivegent.config import settings
from hivegent.server.routes.conversations import _accept_attachments


def _request(*content: str | BinaryContent) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(content=list(content))])]


def test_image_is_admitted_and_sanitized_in_place() -> None:
    """The rewrite lands on the message the adapter caches, past the prompt text."""
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "x" * 4096)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 128, 255)).save(buffer, format="PNG", pnginfo=info)

    messages = _request(
        "look at this", BinaryContent(data=buffer.getvalue(), media_type="image/png")
    )
    _accept_attachments(messages)

    part = messages[0].parts[0]
    assert isinstance(part, UserPromptPart)
    assert isinstance(part.content, list)
    attached = part.content[1]
    assert isinstance(attached, BinaryContent)
    assert b"Comment" not in attached.data


@pytest.mark.parametrize("media_type", ["application/pdf", "image/svg+xml"])
def test_non_ingestible_type_is_rejected(media_type: str) -> None:
    """A document, and an image no vision backend ingests, are both refused."""
    with pytest.raises(HTTPException) as exc:
        _accept_attachments(_request(BinaryContent(data=b"x", media_type=media_type)))

    assert exc.value.status_code == 400
    assert "workspace" in str(exc.value.detail)


def test_oversized_image_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is checked before the bytes are parsed as an image."""
    monkeypatch.setattr(settings.limits, "max_attachment_bytes", 16)

    with pytest.raises(HTTPException) as exc:
        _accept_attachments(
            _request(BinaryContent(data=b"x" * 32, media_type="image/png"))
        )

    assert exc.value.status_code == 400
