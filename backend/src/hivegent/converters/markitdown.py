"""MarkItDown-based document converter."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from markitdown import MarkItDown
from pydantic import BaseModel

from .base import DocumentConverter

__all__ = ["MarkItDownConverter", "MarkItDownConverterConfig"]


class MarkItDownConverterConfig(BaseModel):
    """Configuration for the MarkItDown conversion pipeline."""


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

    def _convert_sync(self, path: Path) -> str:
        """Run the synchronous MarkItDown conversion."""
        md = MarkItDown()
        result = md.convert(str(path))
        return str(result.text_content)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> str:
        """Convert a document to markdown using MarkItDown.

        Args:
            path: Path to the document to convert.

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path)
