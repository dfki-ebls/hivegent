"""PNG sanitization helpers for vision-model inputs."""

from __future__ import annotations

import mimetypes

__all__ = ["guess_image_media_type", "sanitize_image_bytes"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_TEXT_CHUNK_TYPES = frozenset({b"tEXt", b"zTXt", b"iTXt"})
_PNG_IEND = b"IEND"


def _strip_png_text_chunks(image_bytes: bytes) -> bytes:
    """Remove text chunks from a PNG without decoding pixel data."""
    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature")

    output = bytearray(_PNG_SIGNATURE)
    offset = len(_PNG_SIGNATURE)
    end = len(image_bytes)
    saw_iend = False

    while offset < end:
        if offset + 8 > end:
            raise ValueError("Truncated PNG chunk header")
        chunk_length = int.from_bytes(image_bytes[offset : offset + 4], "big")
        chunk_type = image_bytes[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > end:
            raise ValueError("Truncated PNG chunk data")
        if chunk_type not in _PNG_TEXT_CHUNK_TYPES:
            output.extend(image_bytes[offset:chunk_end])
        offset = chunk_end
        if chunk_type == _PNG_IEND:
            saw_iend = True
            break

    if not saw_iend:
        raise ValueError("PNG is missing IEND")

    return bytes(output)


def guess_image_media_type(path: str) -> str | None:
    """Guess the MIME type for an image path."""
    media_type = mimetypes.guess_type(path)[0]
    if media_type and media_type.startswith("image/"):
        return media_type
    return None


def sanitize_image_bytes(image_bytes: bytes, media_type: str) -> bytes:
    """Strip PNG metadata that can break Pillow-based inference servers.

    Non-PNG inputs are returned unchanged.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.

    Returns:
        Sanitized image bytes.

    Raises:
        ValueError: If a PNG is structurally invalid.
    """
    if media_type == "image/png":
        return _strip_png_text_chunks(image_bytes)
    return image_bytes
