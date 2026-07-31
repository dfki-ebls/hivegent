"""Project supported Unicode and Western text as Markdown."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ..text import NOT_TEXT_REASON, decode_bytes
from .base import ConversionResult, DocumentConverter, fenced_code_block

__all__ = ["PlainTextConverter", "convert_plain_text"]


def convert_plain_text(content: bytes, suffix: str) -> ConversionResult | None:
    """Decode bytes and project plain text into a fenced Markdown block."""
    decoded = decode_bytes(content)
    if decoded is None:
        return None

    return ConversionResult(
        markdown=fenced_code_block(decoded.text, suffix),
        source_encoding=decoded.source_encoding,
    )


@dataclass(slots=True, frozen=True)
class PlainTextConverter(DocumentConverter):
    """Converter that reads supported Unicode and Western text verbatim.

    Its extensions define AUTO routing priority, while
    :attr:`accepts_any_extension` lets explicit uses handle any filename.
    Unsupported or binary-looking bytes raise.
    """

    name: ClassVar[str] = "plain-text"
    label: ClassVar[str] = "Plain Text"
    description: ClassVar[str] = (
        "Text, configuration, data-serialization, and source files as-is"
    )
    # These overlap richer converters and therefore need an explicit AUTO
    # priority.  Every other suffix already reaches plain text through AUTO's
    # default.
    extensions: ClassVar[frozenset[str]] = frozenset(
        {".json", ".text", ".txt", ".xml"}
    )
    accepts_any_extension: ClassVar[bool] = True

    def _convert_sync(self, path: Path, /) -> ConversionResult:
        result = convert_plain_text(path.read_bytes(), path.suffix)
        if result is None:
            msg = f"{path.name} {NOT_TEXT_REASON}"
            raise ValueError(msg)

        return result
