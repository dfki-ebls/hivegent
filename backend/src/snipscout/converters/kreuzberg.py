"""Kreuzberg-based document converter with native async support."""

from pathlib import Path

from .base import DocumentConverter

__all__ = ["KreuzbergConverter"]


class KreuzbergConverter(DocumentConverter):
    """Document converter using the Kreuzberg text extraction library.

    Kreuzberg extracts text from 75+ file formats including Office documents,
    PDFs, images (with OCR), and many more. It provides a native async API,
    so no thread wrapping is needed.
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "kreuzberg"

    async def convert(self, file_path: Path) -> str:
        """Convert a document to plain text using Kreuzberg.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The extracted text content.

        Raises:
            ImportError: If kreuzberg is not installed.
        """
        try:
            from kreuzberg import extract_file
        except ImportError as e:
            raise ImportError(
                "kreuzberg is not installed. "
                "Install with: pip install kreuzberg"
            ) from e

        result = await extract_file(file_path)
        return str(result.content)
