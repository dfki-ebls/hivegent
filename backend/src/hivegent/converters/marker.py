"""Marker-based PDF converter."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from marker.converters.pdf import (  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
    PdfConverter,
)
from marker.models import (  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
    create_model_dict,
)
from pydantic import BaseModel

from .base import ConversionResult, DocumentConverter, ExtractedImage, pil_to_png_bytes

__all__ = ["MarkerConverter", "MarkerConverterConfig"]


class MarkerConverterConfig(BaseModel):
    """Configuration for the Marker conversion pipeline."""


@lru_cache(maxsize=1)
def _build_converter(device: str) -> PdfConverter:
    """Build a Marker PDF converter; cached because model loading is expensive.

    ``device`` places the surya models; ``"auto"`` passes ``None`` so Marker
    self-detects via ``TORCH_DEVICE``/CUDA (governed by the process env).
    Marker's other shared-setting analogues do not map cleanly: page batch
    sizes are governed by surya's own env vars and its OCR model is
    multilingual, so ``compute.batch_size`` and ``ocr.languages`` are
    intentionally ignored.
    """
    artifacts = create_model_dict(device=None if device == "auto" else device)
    return PdfConverter(artifact_dict=artifacts)


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
    device: str = field(default="auto", kw_only=True)
    """Compute device for the models (``"auto"`` self-detects); code-level, not a setting."""

    def _convert_sync(self, path: Path) -> ConversionResult:
        result = _build_converter(self.device)(str(path))
        image_data = {
            p: ExtractedImage(data=pil_to_png_bytes(img))
            for p, img in result.images.items()
        }
        return ConversionResult(markdown=str(result.markdown), images=image_data)
