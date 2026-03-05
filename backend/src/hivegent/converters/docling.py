"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from docling.datamodel.base_models import FormatToExtensions, InputFormat
from docling.datamodel.pipeline_options import (
    ConvertPipelineOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import DocumentConverter as DoclingDocumentConverter
from docling_core.types.doc import ImageRefMode, PictureItem
from pydantic import BaseModel, Field

from .base import ConversionResult, DocumentConverter, pil_to_png_bytes

__all__ = ["DoclingConverter", "DoclingConverterConfig"]


class DoclingConverterConfig(BaseModel):
    """Configuration for the Docling conversion pipeline.

    Uses docling's own Pydantic option models.
    ``pdf_options`` applies to PDF and image formats;
    ``convert_options`` applies to Office and text formats.
    """

    pdf_options: ThreadedPdfPipelineOptions = Field(
        default_factory=ThreadedPdfPipelineOptions,
        description="Options for PDF and image formats (OCR, table structure, layout, etc.)",
    )
    convert_options: ConvertPipelineOptions = Field(
        default_factory=ConvertPipelineOptions,
        description="Options for Office and text formats (DOCX, PPTX, HTML, etc.)",
    )


# Formats that use the threaded PDF pipeline options.
_PDF_FORMATS = frozenset({InputFormat.PDF, InputFormat.IMAGE, InputFormat.METS_GBS})


# Derived from docling.datamodel.base_models.FormatToExtensions.
# https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py
@dataclass(slots=True, frozen=True)
class DoclingConverter(DocumentConverter):
    """Document converter using the Docling library.

    Docling provides high-quality document conversion with excellent support
    for Office documents (DOCX, XLSX, PPTX), PDFs, and images.
    """

    name = "docling"
    extensions = frozenset(
        f".{ext}" for exts in FormatToExtensions.values() for ext in exts
    )
    config: DoclingConverterConfig = field(default_factory=DoclingConverterConfig)

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous Docling conversion."""
        # Start from default format options (which include the correct backend
        # and pipeline_cls) and only override pipeline_options.
        converter = DoclingDocumentConverter()
        for fmt in converter.format_to_options:
            default = converter.format_to_options[fmt]
            opts = (
                self.config.pdf_options
                if fmt in _PDF_FORMATS
                else self.config.convert_options
            )
            if fmt in _PDF_FORMATS:
                opts = opts.model_copy(update={"generate_picture_images": True})
            converter.format_to_options[fmt] = default.model_copy(
                update={"pipeline_options": opts}
            )

        result = converter.convert(str(path))
        doc = result.document

        # Export markdown with image references (not embedded base64).
        markdown = str(doc.export_to_markdown(image_mode=ImageRefMode.REFERENCED))

        # Collect picture images from the document.
        image_data: dict[str, bytes] = {}
        for item, _level in doc.iterate_items():
            if isinstance(item, PictureItem) and item.image is not None:
                pil_img = item.get_image(doc)
                if pil_img is not None:
                    img_name = f"picture_{item.self_ref}.png"
                    image_data[img_name] = pil_to_png_bytes(pil_img)

        return ConversionResult(markdown=markdown, images=image_data)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a document to markdown using Docling.

        Args:
            path: Path to the document to convert.

        Returns:
            The conversion result with markdown and extracted images.
        """
        return await asyncio.to_thread(self._convert_sync, path)
