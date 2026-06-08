"""Docling-based document converter."""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import PIL.Image
import PIL.ImageFile
from docling.datamodel.base_models import FormatToExtensions, InputFormat
from docling.datamodel.pipeline_options import (
    ConvertPipelineOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import DocumentConverter as DoclingDocumentConverter
from docling_core.types.doc import DoclingDocument, PictureItem
from docling_core.types.doc.labels import PictureClassificationLabel
from pydantic import BaseModel, Field

from ..config import settings
from .base import (
    AssetBBox,
    AssetRole,
    ConversionResult,
    DocumentConverter,
    ExtractedImage,
    pil_to_png_bytes,
)


def _default_pdf_options() -> ThreadedPdfPipelineOptions:
    """PDF/image options with the picture classifier enabled by default.

    The classifier feeds the asset-triage layer so that decorative
    classes (icons, logos, signatures, page thumbnails …) can skip the
    expensive vision-model description step.  Users can disable the
    classifier per-request through the existing per-pipeline config UI.
    """
    return ThreadedPdfPipelineOptions(do_picture_classification=True)


# Raise Pillow's decompression-bomb limit so that large embedded images/streams
# inside PDFs (common with scanned pages) do not trigger DecompressionBombError.
# The default ~178M pixels is too restrictive; the configured value still
# guards against truly degenerate files.
PIL.Image.MAX_IMAGE_PIXELS = settings.limits.max_image_pixels

# Raise the safe decompression block size for PNG text chunks (iTXt/zTXt).
# The default 1 MB causes "Decompressed Data Too Large" for images with
# large embedded metadata (common in Office documents). We are generous
# while still guarding against decompression bombs.
PIL.ImageFile.SAFEBLOCK = 32 * 1024 * 1024  # ty: ignore[invalid-assignment]

__all__ = ["DoclingConverter", "DoclingConverterConfig"]


class DoclingConverterConfig(BaseModel):
    """Configuration for the Docling conversion pipeline.

    Uses docling's own Pydantic option models.
    ``pdf_options`` applies to PDF and image formats;
    ``convert_options`` applies to Office and text formats.
    """

    pdf_options: ThreadedPdfPipelineOptions = Field(
        default_factory=_default_pdf_options,
        description="Options for PDF and image formats (OCR, table structure, layout, etc.)",
    )
    convert_options: ConvertPipelineOptions = Field(
        default_factory=ConvertPipelineOptions,
        description="Options for Office and text formats (DOCX, PPTX, HTML, etc.)",
    )

    def __hash__(self) -> int:
        # Content hash so instances can key the ``_build_converter`` LRU cache.
        return hash(self.model_dump_json())


# Formats that use the threaded PDF pipeline options.
_PDF_FORMATS = frozenset({InputFormat.PDF, InputFormat.IMAGE, InputFormat.METS_GBS})


@lru_cache(maxsize=4)
def _build_converter(config: DoclingConverterConfig) -> DoclingDocumentConverter:
    """Build and configure a Docling converter, cached by config.

    Keyed on the live ``config`` object (hashable via its JSON form) and
    built from it directly — re-parsing JSON would degrade polymorphic
    option fields such as ``picture_description_options`` to their base
    class, which docling's pipeline cannot consume.
    """
    converter = DoclingDocumentConverter()
    # Start from default format options (which include the correct backend
    # and pipeline_cls) and only override pipeline_options.
    for fmt in converter.format_to_options:
        default = converter.format_to_options[fmt]
        opts = config.pdf_options if fmt in _PDF_FORMATS else config.convert_options
        if fmt in _PDF_FORMATS:
            opts = opts.model_copy(update={"generate_picture_images": True})
        converter.format_to_options[fmt] = default.model_copy(
            update={"pipeline_options": opts}
        )
    return converter


# Derived from docling.datamodel.base_models.FormatToExtensions.
# https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py
@dataclass(slots=True, frozen=True)
class DoclingConverter(DocumentConverter):
    """Document converter using the Docling library.

    Docling provides high-quality document conversion with excellent support
    for Office documents (DOCX, XLSX, PPTX), PDFs, and images.
    """

    name = "docling"
    label = "Docling"
    description = "Best for Office documents"
    extensions = frozenset(
        f".{ext}" for exts in FormatToExtensions.values() for ext in exts
    )
    config: DoclingConverterConfig = field(default_factory=DoclingConverterConfig)

    def _convert_sync(self, path: Path) -> ConversionResult:
        converter = _build_converter(self.config)
        result = converter.convert(str(path))
        doc = result.document

        # Export markdown with default placeholder mode (``<!-- image -->``).
        markdown = str(doc.export_to_markdown())

        # Replace each placeholder with a proper reference by iterating
        # PictureItems in document order (same order as the placeholders).
        image_data: dict[str, ExtractedImage] = {}
        for item, _ in doc.iterate_items():
            if not isinstance(item, PictureItem):
                continue
            pil_img = item.get_image(doc)
            if pil_img is None:
                continue
            img_name = f"image_{len(image_data):06}.png"
            image_data[img_name] = ExtractedImage(
                data=pil_to_png_bytes(pil_img),
                role=_picture_role(item),
                bbox=_normalized_bbox(item, doc),
                page_no=int(item.prov[0].page_no) if item.prov else None,
                caption=_caption_text(item, doc),
            )
            markdown = markdown.replace("<!-- image -->", f"![]({img_name})", 1)

        return ConversionResult(markdown=markdown, images=image_data)

    async def _convert(self, path: Path, /) -> ConversionResult:
        return await asyncio.to_thread(self._convert_sync, path)


# Docling's native picture-classifier vocabulary partitioned onto our
# two-role enum.  Labels outside both sets fall back to
# :attr:`AssetRole.UNKNOWN` and the triage layer's byte-level
# heuristics.  This is the only place in the codebase that mentions
# ``PictureClassificationLabel`` values.
_DECORATIVE_LABELS = frozenset(
    {
        PictureClassificationLabel.ICON,
        PictureClassificationLabel.LOGO,
        PictureClassificationLabel.SIGNATURE,
        PictureClassificationLabel.STAMP,
        PictureClassificationLabel.BAR_CODE,
        PictureClassificationLabel.QR_CODE,
        PictureClassificationLabel.PAGE_THUMBNAIL,
    }
)
_INFORMATIVE_LABELS = frozenset(
    {
        PictureClassificationLabel.BAR_CHART,
        PictureClassificationLabel.BOX_PLOT,
        PictureClassificationLabel.FLOW_CHART,
        PictureClassificationLabel.LINE_CHART,
        PictureClassificationLabel.PIE_CHART,
        PictureClassificationLabel.SCATTER_PLOT,
        PictureClassificationLabel.SCATTER_CHART,
        PictureClassificationLabel.STACKED_BAR_CHART,
        PictureClassificationLabel.HEATMAP,
        PictureClassificationLabel.TABLE,
        PictureClassificationLabel.ENGINEERING_DRAWING,
        PictureClassificationLabel.CAD_DRAWING,
        PictureClassificationLabel.ELECTRICAL_DIAGRAM,
        PictureClassificationLabel.CHEMISTRY_STRUCTURE,
        PictureClassificationLabel.MARKUSH_STRUCTURE,
        PictureClassificationLabel.MOLECULAR_STRUCTURE,
        PictureClassificationLabel.PHOTOGRAPH,
        PictureClassificationLabel.NATURAL_IMAGE,
        PictureClassificationLabel.FULL_PAGE_IMAGE,
        PictureClassificationLabel.SCREENSHOT_FROM_COMPUTER,
        PictureClassificationLabel.SCREENSHOT_FROM_MANUAL,
        PictureClassificationLabel.SCREENSHOT,
        PictureClassificationLabel.GEOGRAPHICAL_MAP,
        PictureClassificationLabel.GEOGRAPHIC_MAP,
        PictureClassificationLabel.TOPOGRAPHICAL_MAP,
        PictureClassificationLabel.REMOTE_SENSING,
        PictureClassificationLabel.MUSIC,
    }
)
_DOCLING_ROLE_MAP: dict[str, AssetRole] = {
    **{label.value: AssetRole.DECORATIVE for label in _DECORATIVE_LABELS},
    **{label.value: AssetRole.INFORMATIVE for label in _INFORMATIVE_LABELS},
}


def _picture_role(item: PictureItem) -> AssetRole:
    """Map Docling's classifier output for *item* onto :class:`AssetRole`."""
    meta = item.meta
    if (
        meta is None
        or meta.classification is None
        or not meta.classification.predictions
    ):
        return AssetRole.UNKNOWN
    prediction = meta.classification.get_main_prediction()
    return _DOCLING_ROLE_MAP.get(str(prediction.class_name), AssetRole.UNKNOWN)


def _normalized_bbox(item: PictureItem, doc: DoclingDocument) -> AssetBBox | None:
    """Return the first-provenance bbox normalized to top-left ``[0, 1]`` page coords."""
    if not item.prov:
        return None
    prov = item.prov[0]
    page = doc.pages.get(prov.page_no)
    if page is None or page.size is None:
        return None
    normalized = prov.bbox.to_top_left_origin(page_height=page.size.height).normalized(
        page.size
    )
    return AssetBBox(
        x_min=float(normalized.l),
        y_min=float(normalized.t),
        x_max=float(normalized.r),
        y_max=float(normalized.b),
    )


def _caption_text(item: PictureItem, doc: DoclingDocument) -> str | None:
    """Return the resolved caption text for *item*, normalized to ``None`` if empty."""
    text = item.caption_text(doc).strip()
    return text or None
