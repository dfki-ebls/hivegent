"""MarkItDown-based document converter."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from markitdown import MarkItDown
from pydantic import BaseModel

from .base import ConversionResult, DocumentConverter

__all__ = ["MarkItDownConverter", "MarkItDownConverterConfig"]


class MarkItDownConverterConfig(BaseModel):
    """Configuration for the MarkItDown conversion pipeline."""


@lru_cache(maxsize=4)
def _build_converter() -> MarkItDown:
    """Build a MarkItDown converter; cached for reuse across calls."""
    return MarkItDown()


# MarkItDown has no public format listing API. Each converter in
# markitdown.converters defines its own ACCEPTED_FILE_EXTENSIONS constant.
# https://github.com/microsoft/markitdown/tree/main/packages/markitdown/src/markitdown/converters
@dataclass(slots=True, frozen=True)
class MarkItDownConverter(DocumentConverter):
    """Document converter using Microsoft's MarkItDown library.

    MarkItDown converts Office documents, PDFs, images, HTML, and other
    formats to markdown.
    """

    name = "markitdown"
    label = "MarkItDown"
    description = "Microsoft's converter for Office, PDF, images, and more"
    extensions = frozenset(
        {
            ".pdf",
            ".docx",
            ".xlsx",
            ".xls",
            ".pptx",
            ".html",
            ".htm",
            ".csv",
            ".json",
            ".jsonl",
            ".ndjson",
            ".xml",
            ".rss",
            ".atom",
            ".epub",
            ".ipynb",
            ".zip",
            ".txt",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
            ".wav",
            ".mp3",
            ".m4a",
            ".msg",
        }
    )
    config: MarkItDownConverterConfig = field(default_factory=MarkItDownConverterConfig)

    def _convert_sync(self, path: Path) -> ConversionResult:
        result = _build_converter().convert(str(path))
        return ConversionResult(markdown=str(result.text_content))
