"""Marker-based PDF converter."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from marker.converters.pdf import PdfConverter  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
from marker.models import create_model_dict  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
from pydantic import BaseModel

from .base import ConversionResult, DocumentConverter, pil_to_png_bytes

__all__ = ["MarkerConverter", "MarkerConverterConfig"]


class MarkerConverterConfig(BaseModel):
    """Configuration for the Marker conversion pipeline."""


@lru_cache(maxsize=4)
def _build_converter() -> PdfConverter:
    """Build a Marker PDF converter; cached because model loading is expensive."""
    return PdfConverter(artifact_dict=create_model_dict())


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
    label = "Marker"
    description = "Best for PDF documents"
    extensions = frozenset({".pdf"})
    config: MarkerConverterConfig = field(default_factory=MarkerConverterConfig)

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous Marker conversion."""
        result = _build_converter()(str(path))
        image_data = {p: pil_to_png_bytes(img) for p, img in result.images.items()}
        return ConversionResult(markdown=str(result.markdown), images=image_data)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a PDF document to markdown using Marker.

        Args:
            path: Path to the PDF document to convert.

        Returns:
            The conversion result with markdown and extracted images.
        """
        return await asyncio.to_thread(self._convert_sync, path)
