"""Kreuzberg-based document converter with native async support."""

from dataclasses import dataclass
from pathlib import Path

from kreuzberg import extract_file

from .base import DocumentConverter

__all__ = ["KreuzbergConverter"]


# Kreuzberg exposes get_extensions_for_mime() per MIME type but has no
# API to enumerate all supported types at once.
# https://docs.kreuzberg.dev/features/supported-formats/
@dataclass(slots=True, frozen=True)
class KreuzbergConverter(DocumentConverter):
    """Document converter using the Kreuzberg text extraction library.

    Kreuzberg extracts text from 75+ file formats including Office documents,
    PDFs, images (with OCR), and many more. It provides a native async API,
    so no thread wrapping is needed.
    """

    name = "kreuzberg"
    extensions = frozenset(
        {
            ".pdf",
            ".docx",
            ".xlsx",
            ".pptx",
            ".doc",
            ".xls",
            ".ppt",
            ".odt",
            ".ods",
            ".html",
            ".htm",
            ".xml",
            ".json",
            ".csv",
            ".epub",
            ".rtf",
            ".txt",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".tiff",
            ".tif",
            ".bmp",
            ".svg",
            ".ico",
            ".msg",
            ".eml",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
        }
    )

    async def __call__(self, path: Path, /) -> str:
        """Convert a document to plain text using Kreuzberg.

        Args:
            path: Path to the document to convert.

        Returns:
            The extracted text content.
        """
        result = await extract_file(path)
        return str(result.content)
