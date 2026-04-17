"""Unit tests for the PNG sanitizer."""

import io
import struct
import zlib

from PIL import Image, PngImagePlugin

from hivegent.converters.images import sanitize_image_bytes


def test_strips_oversized_iccp_chunk() -> None:
    """Oversized iCCP chunks are removed so Pillow can reopen under default caps."""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 0, 0)).save(buffer, format="PNG")
    source = buffer.getvalue()

    ihdr_end = 8 + 4 + 4 + struct.unpack(">I", source[8:12])[0] + 4
    profile = b"ICC\x00\x00" + zlib.compress(b"B" * (4 * 1024 * 1024))
    chunk = (
        struct.pack(">I", len(profile))
        + b"iCCP"
        + profile
        + struct.pack(">I", zlib.crc32(b"iCCP" + profile) & 0xFFFFFFFF)
    )
    poisoned = source[:ihdr_end] + chunk + source[ihdr_end:]

    sanitized = sanitize_image_bytes(poisoned, "image/png")

    old_limit = PngImagePlugin.MAX_TEXT_CHUNK
    setattr(PngImagePlugin, "MAX_TEXT_CHUNK", 2 * 1024 * 1024)
    try:
        with Image.open(io.BytesIO(sanitized)) as img:
            img.load()
            assert img.size == (32, 32)
    finally:
        setattr(PngImagePlugin, "MAX_TEXT_CHUNK", old_limit)
