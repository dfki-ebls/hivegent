"""Unit tests for the chat composer's attachment gate."""

import pytest
from fastapi import HTTPException
from PIL.PngImagePlugin import PngInfo
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, UserPromptPart

from hivegent.config import settings
from hivegent.server.routes.conversations import _accept_attachments
from tests.helpers import png_bytes


def _request(*content: str | BinaryContent) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(content=list(content))])]


def test_image_is_admitted_and_sanitized_in_place() -> None:
    """The rewrite lands on the message the adapter caches, past the prompt text."""
    info = PngInfo()
    info.add_text("Comment", "x" * 4096)

    messages = _request(
        "look at this", BinaryContent(data=png_bytes(info), media_type="image/png")
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


def test_more_images_than_the_gateway_takes_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count is the gateway's, refused here rather than mid-stream.

    A request over the per-request image cap is rejected whole by the serving
    gateway, so the turn would fail once it was already running.
    """
    monkeypatch.setattr(settings.multimodal, "max_images", 1)
    png = png_bytes()

    _accept_attachments(_request(BinaryContent(data=png, media_type="image/png")))

    with pytest.raises(HTTPException) as exc:
        _accept_attachments(
            _request(
                BinaryContent(data=png, media_type="image/png"),
                BinaryContent(data=png, media_type="image/png"),
            )
        )

    assert exc.value.status_code == 400
    assert "At most 1 image" in str(exc.value.detail)
