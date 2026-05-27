"""PNG sanitization helpers for vision-model inputs."""

import mimetypes

__all__ = ["guess_image_media_type", "sanitize_image_bytes"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Critical chunks (IHDR/PLTE/IDAT/IEND) plus tRNS for transparent rendering.
# Everything else — ICC profiles, text, EXIF, APNG animation, timestamps,
# color-space hints — is ancillary and safe to drop, which sidesteps Pillow's
# per-chunk decompression caps (e.g. iCCP and zTXt/iTXt payloads embedded in
# Office documents regularly exceed the default MAX_TEXT_CHUNK of 1 MB).
_PNG_KEPT_CHUNK_TYPES = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"})


def _strip_png_metadata(image_bytes: bytes) -> bytes:
    """Keep only essential PNG chunks; discard all ancillary metadata."""
    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature")

    output = bytearray(_PNG_SIGNATURE)
    offset = len(_PNG_SIGNATURE)
    end = len(image_bytes)

    while offset + 8 <= end:
        chunk_length = int.from_bytes(image_bytes[offset : offset + 4], "big")
        chunk_type = image_bytes[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > end:
            raise ValueError("Truncated PNG chunk data")
        if chunk_type in _PNG_KEPT_CHUNK_TYPES:
            output.extend(image_bytes[offset:chunk_end])
        offset = chunk_end
        if chunk_type == b"IEND":
            return bytes(output)

    raise ValueError("PNG is missing IEND")


def guess_image_media_type(path: str) -> str | None:
    """Guess the MIME type for an image path."""
    media_type = mimetypes.guess_type(path)[0]
    if media_type and media_type.startswith("image/"):
        return media_type
    return None


def sanitize_image_bytes(image_bytes: bytes, media_type: str) -> bytes:
    """Strip PNG ancillary chunks that can trip Pillow-based inference servers.

    Non-PNG inputs are returned unchanged: JPEG, WebP, and GIF have their own
    container conventions that the vision providers handle natively.

    Raises:
        ValueError: If a PNG is structurally invalid.
    """
    if media_type == "image/png":
        return _strip_png_metadata(image_bytes)
    return image_bytes
