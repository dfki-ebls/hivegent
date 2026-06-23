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
    PngImagePlugin.MAX_TEXT_CHUNK = 2 * 1024 * 1024  # ty: ignore[invalid-assignment]
    try:
        with Image.open(io.BytesIO(sanitized)) as img:
            img.load()
            assert img.size == (32, 32)
    finally:
        PngImagePlugin.MAX_TEXT_CHUNK = old_limit


def test_malformed_png_falls_back_to_original() -> None:
    """A structurally broken PNG is returned verbatim, never raised on.

    Sanitisation is best-effort; a quirky-but-storable image must not fail
    the upload that carries it.
    """
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x20IDAT truncated"

    assert sanitize_image_bytes(broken, "image/png") == broken


def test_non_png_bytes_declared_png_fall_back_to_original() -> None:
    """Bytes declared image/png but lacking a PNG signature pass through.

    A missing signature is a content-type mismatch, not a malformed PNG, so
    the bytes are stored verbatim rather than parsed as chunks.
    """
    not_a_png = b"\xff\xd8\xff\xe0 JFIF-ish, not a PNG"

    assert sanitize_image_bytes(not_a_png, "image/png") == not_a_png
