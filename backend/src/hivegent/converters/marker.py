"""Marker-based PDF converter."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from marker.converters.pdf import PdfConverter  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
from marker.models import create_model_dict  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
from pydantic import BaseModel

from ..config import settings
from .base import ConversionResult, DocumentConverter, ExtractedImage, pil_to_png_bytes

__all__ = ["MarkerConverter", "MarkerConverterConfig"]


class MarkerConverterConfig(BaseModel):
    """Configuration for the Marker conversion pipeline."""


@lru_cache(maxsize=4)
def _build_converter(device: str | None) -> PdfConverter:
    """Build a Marker PDF converter; cached because model loading is expensive.

    ``device`` places the surya models on the shared compute device
    (``None`` lets Marker auto-detect via ``TORCH_DEVICE``/CUDA).  Marker's
    other shared-setting analogues do not map cleanly: page batch sizes are
    governed by surya's own env vars and its OCR model is multilingual, so
    ``compute.batch_size`` and ``ocr.languages`` are intentionally ignored.
    """
    return PdfConverter(artifact_dict=create_model_dict(device=device))


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
        device = settings.conversion.compute.device
        result = _build_converter(None if device == "auto" else device)(str(path))
        image_data = {
            p: ExtractedImage(data=pil_to_png_bytes(img))
            for p, img in result.images.items()
        }
        return ConversionResult(markdown=str(result.markdown), images=image_data)

    async def _convert(self, path: Path, /) -> ConversionResult:
        return await asyncio.to_thread(self._convert_sync, path)
