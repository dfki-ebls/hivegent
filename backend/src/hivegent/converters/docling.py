"""Docling-based document converter."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import PIL.Image
import PIL.PngImagePlugin
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.backend_options import (
    HTMLBackendOptions,
    MarkdownBackendOptions,
    PdfBackendOptions,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    ConvertPipelineOptions,
    TesseractOcrOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import DocumentConverter as DoclingDocumentConverter
from docling_core.types.doc import DoclingDocument, PictureItem
from docling_core.types.doc.labels import PictureClassificationLabel
from pydantic import BaseModel, Field

# Selected converters are loaded before their work is offloaded, so this import
# initializes tesserocr and cysignals on the process main thread.
import tesserocr  # noqa: F401  # isort: skip

from ..config import settings
from .base import (
    AssetBBox,
    AssetRole,
    ConversionResult,
    DocumentConverter,
    ExtractedImage,
    pil_to_png_bytes,
)
from .pdf_classify import pdf_has_text_layer


def _accelerator_options() -> AcceleratorOptions:
    """Accelerator options shared by every docling pipeline.

    AcceleratorOptions defaults its device to AUTO; placement is decided
    centrally by CUDA_VISIBLE_DEVICES, so only the thread count is pulled
    from the shared :class:`~hivegent.config.ComputeSettings` — as the
    per-worker share, so a pool worker sizes torch/onnxruntime to its slice of
    the cores rather than the whole machine.
    """
    return AcceleratorOptions(num_threads=settings.compute.threads_per_worker)


def _default_pdf_options() -> ThreadedPdfPipelineOptions:
    """PDF/image options with the picture classifier enabled by default.

    The classifier feeds the asset-triage layer so that decorative
    classes (icons, logos, signatures, page thumbnails …) can skip the
    expensive vision-model description step.  It only runs when the
    converter's ``detect_asset_roles`` flag is set (triage happens only
    in DESCRIBE asset mode); users can additionally disable it
    per-request through the existing per-pipeline config UI.

    OCR runs in-process through the ``tesserocr`` bindings rather than
    docling's default RapidOCR engine or the Tesseract CLI: RapidOCR
    downloads ONNX models into its read-only package directory at first
    use (fatal under the production unit's ``ProtectSystem=strict``) and
    only ships Chinese/English models, while the CLI engine spawns two
    ``tesseract`` subprocesses (OSD + OCR) and round-trips a PNG through
    a temp file for every OCR rectangle.  tesserocr bundles its own
    libtesseract but no language data; ``path`` is left unset so it
    resolves the nixpkgs tessdata via ``TESSDATA_PREFIX``, which the nix
    package and dev shell both set.
    """
    batch_size = settings.compute.batch_size
    return ThreadedPdfPipelineOptions(
        do_picture_classification=True,
        ocr_options=TesseractOcrOptions(lang=settings.conversion.ocr.languages),
        accelerator_options=_accelerator_options(),
        ocr_batch_size=batch_size,
        layout_batch_size=batch_size,
        table_batch_size=batch_size,
    )


def _default_convert_options() -> ConvertPipelineOptions:
    """Office/text options seeded from the same shared compute settings."""
    return ConvertPipelineOptions(accelerator_options=_accelerator_options())


# Raise Pillow's decompression-bomb limit so that large embedded images/streams
# inside PDFs (common with scanned pages) do not trigger DecompressionBombError.
# The default ~178M pixels is too restrictive; the configured value still
# guards against truly degenerate files.
PIL.Image.MAX_IMAGE_PIXELS = settings.limits.max_image_pixels

# Raise the safe decompression size for PNG text chunks (iTXt/zTXt).  The
# default 1 MB causes "Decompressed Data Too Large" for images with large
# embedded metadata (common in Office documents).  Setting the derived
# constants rather than ``ImageFile.SAFEBLOCK`` is deliberate: PngImagePlugin
# reads SAFEBLOCK once at import time, so writing it only takes effect while
# that module happens to be unimported.  The total stays at Pillow's default,
# so a raised per-chunk bound does not lift the per-image one.
PIL.PngImagePlugin.MAX_TEXT_CHUNK = 32 * 1024 * 1024  # ty: ignore[invalid-assignment]
PIL.PngImagePlugin.MAX_TEXT_MEMORY = 64 * 1024 * 1024


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
        default_factory=_default_convert_options,
        description="Options for Office and text formats (DOCX, PPTX, HTML, etc.)",
    )

    def __hash__(self) -> int:
        # Content hash so instances can key the ``_build_converter`` LRU cache.
        # Computed fields are views over stored ones, and docling deprecates one.
        return hash(self.model_dump_json(exclude_computed_fields=True))


# Formats that use the threaded PDF pipeline options.
_PDF_FORMATS = frozenset({InputFormat.PDF, InputFormat.IMAGE, InputFormat.METS_GBS})
# Backends that bound image pixels with an option of their own, ignoring
# Pillow's global limit.
_PIXEL_BOUNDED_BACKENDS = {
    InputFormat.IMAGE: PdfBackendOptions,
    InputFormat.HTML: HTMLBackendOptions,
    InputFormat.MHTML: HTMLBackendOptions,
    InputFormat.MD: MarkdownBackendOptions,
}
# Formats docling reads only through what the project does not ship: audio and
# video through a Whisper backend (videos are sampled into frames instead, see
# `converters.video`), and legacy Office, RTF and OpenDocument through
# LibreOffice or odfdo (anydoc reads those instead).
_UNSHIPPED_FORMATS = frozenset(
    {
        InputFormat.AUDIO,
        InputFormat.VIDEO,
        InputFormat.DOC,
        InputFormat.RTF,
        InputFormat.PPT,
        InputFormat.XLS,
        InputFormat.ODT,
        InputFormat.ODS,
        InputFormat.ODP,
    }
)
_ALLOWED_FORMATS = tuple(fmt for fmt in InputFormat if fmt not in _UNSHIPPED_FORMATS)


# Bounded low: each cached converter holds its own resident layout/table
# (and optional picture-classifier) torch models, so every extra config
# variant is another full model set in VRAM.  Two slots cover the common
# OCR-on / OCR-off split without letting model memory creep upward.
@lru_cache(maxsize=2)
def _build_converter(config: DoclingConverterConfig) -> DoclingDocumentConverter:
    """Build and configure a Docling converter, cached by config.

    Keyed on the live ``config`` object (hashable via its JSON form) and
    built from it directly — re-parsing JSON would degrade polymorphic
    option fields such as ``picture_description_options`` to their base
    class, which docling's pipeline cannot consume.
    """
    converter = DoclingDocumentConverter(allowed_formats=list(_ALLOWED_FORMATS))
    # Start from default format options (which include the correct backend
    # and pipeline_cls) and only override pipeline and backend options.
    for fmt in converter.format_to_options:
        default = converter.format_to_options[fmt]
        opts = config.pdf_options if fmt in _PDF_FORMATS else config.convert_options

        if fmt in _PDF_FORMATS:
            opts = opts.model_copy(update={"generate_picture_images": True})

        update: dict[str, Any] = {"pipeline_options": opts}

        if (backend_options := _PIXEL_BOUNDED_BACKENDS.get(fmt)) is not None:
            # Validated from a mapping, since docling declares its defaults
            # positionally in `Field`, which type checkers do not read.
            update["backend_options"] = backend_options.model_validate(
                {"max_image_pixels": settings.limits.max_image_pixels}
            )

        converter.format_to_options[fmt] = default.model_copy(update=update)
    return converter


@dataclass(slots=True, frozen=True)
class DoclingConverter(DocumentConverter):
    """Document converter using the Docling library.

    Docling provides high-quality document conversion with excellent support
    for Office documents (DOCX, XLSX, PPTX), PDFs, and images.
    """

    name = "docling"
    config: DoclingConverterConfig = field(default_factory=DoclingConverterConfig)
    device: str = field(default="auto", kw_only=True)
    """Compute device for the models (``"auto"`` self-detects); code-level, not a setting."""

    def _convert_sync(self, path: Path) -> ConversionResult:
        config = self.config
        pdf_overrides: dict[str, Any] = {}

        if not self.detect_asset_roles and config.pdf_options.do_picture_classification:
            # The classifier's labels only feed asset triage, which runs in
            # DESCRIBE mode; skip the model entirely otherwise.
            pdf_overrides["do_picture_classification"] = False

        if (
            config.pdf_options.do_ocr
            and settings.conversion.ocr.skip_native_text
            and path.suffix.lower() == ".pdf"
            and pdf_has_text_layer(path)
        ):
            # Born-digital PDF: the text layer is authoritative, so skip the
            # Tesseract stage entirely (scanned/image-only PDFs keep it).
            pdf_overrides["do_ocr"] = False

        if self.device != "auto":
            # Pin placement away from docling's AUTO default; folded into the
            # config so it is part of the build cache key.
            pdf_overrides["accelerator_options"] = (
                config.pdf_options.accelerator_options.model_copy(
                    update={"device": AcceleratorDevice(self.device)}
                )
            )

        if pdf_overrides:
            config = config.model_copy(
                update={
                    "pdf_options": config.pdf_options.model_copy(update=pdf_overrides)
                }
            )

        converter = _build_converter(config)
        result = converter.convert(str(path))
        doc = result.document

        # Export markdown with default placeholder mode (``<!-- image -->``),
        # and unpadded tables, whose padding would chunk into whitespace.
        markdown = str(doc.export_to_markdown(compact_tables=True))

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
