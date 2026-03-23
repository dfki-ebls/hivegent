"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import PIL.Image
import PIL.ImageFile
from docling.datamodel.base_models import FormatToExtensions, InputFormat
from docling.datamodel.pipeline_options import (
    ConvertPipelineOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import DocumentConverter as DoclingDocumentConverter
from docling_core.types.doc import PictureItem
from pydantic import BaseModel, Field

from .base import ConversionResult, DocumentConverter, pil_to_png_bytes

# Raise Pillow's decompression-bomb limit so that large embedded images/streams
# inside PDFs (common with scanned pages) do not trigger DecompressionBombError.
# The default ~178M pixels is too restrictive; 1 billion pixels (~3 GB
# uncompressed) still guards against truly degenerate files.
PIL.Image.MAX_IMAGE_PIXELS = 1_000_000_000

# Raise the safe decompression block size for PNG text chunks (iTXt/zTXt).
# The default 1 MB causes "Decompressed Data Too Large" for images with
# large embedded metadata (common in Office documents). We are generous
# while still guarding against decompression bombs.
PIL.ImageFile.SAFEBLOCK = 32 * 1024 * 1024  # type: ignore[assignment]

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

        # Export markdown with default placeholder mode (``<!-- image -->``).
        markdown = str(doc.export_to_markdown())

        # Replace each placeholder with a proper reference by iterating
        # PictureItems in document order (same order as the placeholders).
        image_data: dict[str, bytes] = {}
        for item, _ in doc.iterate_items():
            if not isinstance(item, PictureItem):
                continue
            pil_img = item.get_image(doc)
            if pil_img is None:
                continue
            img_name = f"image_{len(image_data):06}.png"
            image_data[img_name] = pil_to_png_bytes(pil_img)
            markdown = markdown.replace("<!-- image -->", f"![]({img_name})", 1)

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
