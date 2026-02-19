"""Marker-based PDF converter with lazy loading."""

import asyncio
from pathlib import Path
from typing import Any

from .base import DocumentConverter

__all__ = ["MarkerConverter"]


class MarkerConverter(DocumentConverter):
    """PDF converter using the Marker library.

    Marker provides high-accuracy PDF to markdown conversion with support for
    complex layouts, tables, and equations. This converter uses lazy imports
    to avoid loading the heavy dependencies until needed.
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "marker"

    def __init__(self) -> None:
        """Initialize the converter with lazy loading."""
        self._converter: Any = None

    def _convert_sync(self, file_path: Path) -> str:
        """Run the synchronous Marker conversion.

        Raises:
            ImportError: If marker-pdf is not installed.
        """
        if self._converter is None:
            try:
                from marker.converters.pdf import PdfConverter
                from marker.models import create_model_dict

                self._converter = PdfConverter(artifact_dict=create_model_dict())
            except ImportError as e:
                raise ImportError(
                    "marker-pdf is not installed. Install with: pip install marker-pdf"
                ) from e

        result = self._converter(str(file_path))
        return str(result.markdown)

    async def convert(self, file_path: Path) -> str:
        """Convert a PDF document to markdown using Marker.

        Args:
            file_path: Path to the PDF document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If marker-pdf is not installed.
        """
        return await asyncio.to_thread(self._convert_sync, file_path)
