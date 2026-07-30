"""Decode user-supplied workspace bytes at one shared boundary."""

import codecs
import unicodedata
from dataclasses import dataclass
from pathlib import Path

__all__ = ["NOT_TEXT_REASON", "DecodedText", "decode_bytes", "read_text_file"]

NOT_TEXT_REASON = "is not text-like content"
"""Shared wording for a rejected read, wrapped in each layer's own exception."""

_ALLOWED_CONTROLS = frozenset("\a\b\t\n\v\f\r\x1b")
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclass(slots=True, frozen=True)
class DecodedText:
    """Decoded text tagged with its source encoding."""

    text: str
    """The decoded content, ready to store, index, and re-encode as UTF-8."""

    source_encoding: str | None
    """Canonical source encoding, or ``None`` when already UTF-8."""


def _is_text(text: str) -> bool:
    """Return whether decoded text contains only supported control characters."""
    return all(
        char in _ALLOWED_CONTROLS or unicodedata.category(char) != "Cc"
        for char in text
    )


def _decode_with(content: bytes, encoding: str) -> DecodedText | None:
    """Decode with one codec and reject decoded binary control content."""
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError:
        return None

    if not _is_text(text):
        return None

    return DecodedText(text, encoding)


def decode_bytes(content: bytes) -> DecodedText | None:
    """Decode *content* to text, or return ``None`` when it is not text at all.

    BOM-declared Unicode and strict UTF-8 are deterministic.
    Remaining plausible content uses CP1252, the common Western Windows
    encoding and practical legacy fallback for configuration files and exports.
    Unsupported or binary-looking content returns ``None``.

    >>> decode_bytes(b'{"a": 1}').text
    '{"a": 1}'
    >>> decode_bytes("Grüße".encode("utf-16")).source_encoding
    'utf-16'
    >>> decode_bytes(b"\\x89PNG\\r\\n\\x1a\\n") is None
    True
    >>> decode_bytes(b"text\\x00with nul") is None
    True
    """
    for bom, encoding in _BOM_ENCODINGS:
        if content.startswith(bom):
            return _decode_with(content, encoding)

    try:
        text = content.decode()
    except UnicodeDecodeError:
        return _decode_with(content, "cp1252")

    if not _is_text(text):
        return None

    return DecodedText(text, None)


def read_text_file(path: Path) -> DecodedText | None:
    """Read *path* and decode it, or return ``None`` when it is not text."""
    return decode_bytes(path.read_bytes())
