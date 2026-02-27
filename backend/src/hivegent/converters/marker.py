"""Marker-based PDF converter."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from pydantic import BaseModel

from .base import DocumentConverter

__all__ = ["MarkerConverter", "MarkerConverterConfig"]


class MarkerConverterConfig(BaseModel):
    """Configuration for the Marker conversion pipeline."""


# Marker only converts PDFs. The provider registry lives in
# marker.providers.registry but has no public format listing API.
# https://github.com/VikParuchuri/marker
@dataclass(slots=True, frozen=True)
class MarkerConverter(DocumentConverter):
    """PDF converter using the Marker library.

    Marker provides high-accuracy PDF to markdown conversion with support for
    complex layouts, tables, and equations.
    """

    name = "marker"
    extensions = frozenset({".pdf"})

    def _convert_sync(self, path: Path) -> str:
        """Run the synchronous Marker conversion."""
        converter = PdfConverter(artifact_dict=create_model_dict())
        result = converter(str(path))
        return str(result.markdown)

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a PDF document to markdown using Marker.

        Args:
            path: Path to the PDF document to convert.
            config: Optional pipeline configuration (currently unused).

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path)
