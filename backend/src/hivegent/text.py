"""Decode user-supplied workspace bytes at one shared boundary."""

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MAX_BYTES_PER_CHAR",
    "NOT_TEXT_REASON",
    "DecodedText",
    "decode_bytes",
    "read_text_file",
]

NOT_TEXT_REASON = "is not text-like content"
"""Shared wording for a rejected read, wrapped in each layer's own exception."""

MAX_BYTES_PER_CHAR = 4
"""Widest encoding of one character across the table below.

Lives here because it is a property of that table, not of any caller: it turns
a file size into a lower bound on the character count, which is what lets a
reader with a character budget refuse an oversized file before decoding it.
"""

# The C0 and C1 control blocks (Unicode category ``Cc``) minus the whitespace
# and escape characters that legitimately occur in text: \a \b \t \n \v \f \r \x1b.
_BINARY_CONTROL = re.compile(r"[\x00-\x06\x0e-\x1a\x1c-\x1f\x7f-\x9f]")
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
    return _BINARY_CONTROL.search(text) is None


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
