"""Unit tests for the PNG sanitizer."""

import io
import struct
import zlib

from PIL import Image, PngImagePlugin

from hivegent.converters.images import sanitize_image_bytes


def _solid_png(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Encode a solid-color RGB PNG at the requested size."""
    img = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _inject_ztxt_chunk(png_bytes: bytes, keyword: str, payload: bytes) -> bytes:
    """Insert a zTXt chunk with a custom payload into ``png_bytes``."""
    signature = png_bytes[:8]
    assert signature == b"\x89PNG\r\n\x1a\n"
    index = 8
    length = struct.unpack(">I", png_bytes[index : index + 4])[0]
    chunk_type = png_bytes[index + 4 : index + 8]
    assert chunk_type == b"IHDR"
    after_ihdr = index + 8 + length + 4

    keyword_bytes = keyword.encode("latin-1")
    compressed = zlib.compress(payload)
    chunk_data = keyword_bytes + b"\x00" + b"\x00" + compressed
    chunk = (
        struct.pack(">I", len(chunk_data))
        + b"zTXt"
        + chunk_data
        + struct.pack(">I", zlib.crc32(b"zTXt" + chunk_data) & 0xFFFFFFFF)
    )
    return png_bytes[:after_ihdr] + chunk + png_bytes[after_ihdr:]


def _has_text_chunk(png_bytes: bytes) -> bool:
    """Return whether ``png_bytes`` contains any tEXt, zTXt, or iTXt chunk."""
    with Image.open(io.BytesIO(png_bytes)) as img:
        img.load()
        text = dict(getattr(img, "text", {}) or {})
        info = {key: value for key, value in img.info.items() if isinstance(value, (str, bytes))}
    return bool(text) or bool(info)


def test_strips_oversized_ztxt_chunk() -> None:
    """Sanitizer opens and strips a PNG whose zTXt exceeds Pillow's default cap."""
    payload = b"A" * (4 * 1024 * 1024)
    poisoned = _inject_ztxt_chunk(_solid_png(32, 32), "Comment", payload)
    result = sanitize_image_bytes(poisoned, "image/png")
    default_limit = 2 * 1024 * 1024
    old_limit = PngImagePlugin.MAX_TEXT_CHUNK
    setattr(PngImagePlugin, "MAX_TEXT_CHUNK", default_limit)
    try:
        with Image.open(io.BytesIO(result)) as img:
            img.load()
            assert img.size == (32, 32)
    finally:
        setattr(PngImagePlugin, "MAX_TEXT_CHUNK", old_limit)


def test_strips_text_metadata() -> None:
    """tEXt chunks present on a well-formed PNG are dropped on re-encode."""
    img = Image.new("RGB", (10, 10), (1, 2, 3))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "test")
    info.add_text("Description", "x" * 1024)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", pnginfo=info)
    assert _has_text_chunk(buffer.getvalue())
    result = sanitize_image_bytes(buffer.getvalue(), "image/png")
    with Image.open(io.BytesIO(result)) as out:
        out.load()
        assert not getattr(out, "text", {})


def test_leaves_non_png_bytes_unchanged() -> None:
    """Non-PNG inputs bypass sanitization."""
    payload = b"not a png"
    assert sanitize_image_bytes(payload, "image/jpeg") == payload
