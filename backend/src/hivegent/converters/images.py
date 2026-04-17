"""PNG sanitization helpers for vision-model inputs."""

from __future__ import annotations

import io

from PIL import Image, PngImagePlugin

__all__ = ["sanitize_image_bytes"]

# Some PNGs extracted from PDFs carry text chunks that exceed Pillow's
# default limit. Raise the limit so we can open and strip that metadata.
_MAX_PNG_TEXT_CHUNK = 256 * 1024 * 1024
setattr(PngImagePlugin, "MAX_TEXT_CHUNK", _MAX_PNG_TEXT_CHUNK)


def sanitize_image_bytes(image_bytes: bytes, media_type: str) -> bytes:
    """Strip PNG metadata that can break Pillow-based inference servers.

    Non-PNG inputs are returned unchanged.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.

    Returns:
        Sanitized image bytes.

    Raises:
        OSError: If a PNG cannot be decoded.
        ValueError: If Pillow rejects the PNG.
    """
    if media_type != "image/png":
        return image_bytes

    with Image.open(io.BytesIO(image_bytes)) as img:
        img.load()
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", pnginfo=PngImagePlugin.PngInfo())
        return buffer.getvalue()
