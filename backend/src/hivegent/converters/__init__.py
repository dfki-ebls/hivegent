"""Document conversion infrastructure for Hivegent."""

import importlib
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Protocol, get_type_hints

from pydantic import BaseModel

from .base import (
    IMAGE_MEDIA_TYPES,
    ConversionResult,
    DocumentConverter,
    is_image_suffix,
    is_markdown_suffix,
)
from .video import VIDEO_MEDIA_TYPES, is_video_suffix

__all__ = [
    "INGESTIBLE_IMAGE_MEDIA_TYPES",
    "TABULAR_SUFFIXES",
    "VISION_MEDIA_TYPES",
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "ConversionResult",
    "ConversionSpec",
    "DocumentConverter",
    "EntryProjection",
    "get_conversion_pipelines_info",
    "get_converter",
    "is_tabular",
    "projection_for",
    "projects_verbatim",
    "resolve_auto_pipeline",
    "vision_media_type",
]


class ConversionPipeline(StrEnum):
    """A conversion pipeline, or a recovery fallback's provenance label.

    Most members are selectable pipelines that map to a converter in the
    registry.  A few (LibreOffice, poppler) are recovery *fallbacks*: not
    selectable or routable, so they have no registry entry and exist only to
    label ``conversion_pipeline_used`` on recovered entries (see
    :mod:`hivegent.converters.fallbacks`).
    """

    AUTO = "auto"
    LLM = "llm"
    MARKER = "marker"
    DOCLING = "docling"
    # Recorded on entries recovered by the LibreOffice text fallback; not a
    # selectable pipeline (see converters.fallbacks), so it has no registry entry.
    LIBREOFFICE = "libreoffice"
    # Recorded on PDFs whose garbled glyph-name text was recovered via poppler;
    # a fallback too, likewise not a selectable pipeline.
    POPPLER = "poppler"
    MINERU = "mineru"
    PANDOC = "pandoc"
    MARKITDOWN = "markitdown"
    KREUZBERG = "kreuzberg"
    ANYDOC = "anydoc"
    PDF_INSPECTOR = "pdf-inspector"
    PDF_OXIDE = "pdf-oxide"
    TABLE_CHEF = "table-chef"
    PLAIN_TEXT = "plain-text"


class ConversionSpec(BaseModel):
    """Conversion pipeline selection and configuration."""

    pipeline: ConversionPipeline = ConversionPipeline.AUTO
    config: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ConversionPipelineInfo:
    """Public metadata for a conversion pipeline."""

    value: str
    label: str
    description: str
    extensions: list[str]
    config_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)


class _LlmOptions(Protocol):
    """Subset of LLM options needed to instantiate the LLM converter."""

    model: str
    api_key: str
    base_url: str | None


# Lazy "module:Class" references so heavy imports (marker, docling, mineru, ...)
# only run when the pipeline is actually used.
_CONVERTERS: dict[ConversionPipeline, str] = {
    ConversionPipeline.LLM: "hivegent.converters.llm:LLMConverter",
    ConversionPipeline.PANDOC: "hivegent.converters.pandoc:PandocConverter",
    ConversionPipeline.MARKER: "hivegent.converters.marker:MarkerConverter",
    ConversionPipeline.DOCLING: "hivegent.converters.docling:DoclingConverter",
    ConversionPipeline.MINERU: "hivegent.converters.mineru:MinerUConverter",
    ConversionPipeline.MARKITDOWN: "hivegent.converters.markitdown:MarkItDownConverter",
    ConversionPipeline.KREUZBERG: "hivegent.converters.kreuzberg:KreuzbergConverter",
    ConversionPipeline.ANYDOC: "hivegent.converters.anydoc:AnydocConverter",
    ConversionPipeline.PDF_INSPECTOR: (
        "hivegent.converters.pdf_inspector:PdfInspectorConverter"
    ),
    ConversionPipeline.PDF_OXIDE: "hivegent.converters.pdf_oxide:PdfOxideConverter",
    ConversionPipeline.TABLE_CHEF: "hivegent.converters.chonkie_table:ChonkieTableConverter",
    ConversionPipeline.PLAIN_TEXT: "hivegent.converters.plain_text:PlainTextConverter",
}


# AUTO routing preference: plain-text claims raw-text formats (read as-is),
# docling claims every binary/office format it can handle, and pandoc covers
# the rest.  Routing is derived from each converter's own ``extensions`` (see
# ``_auto_mapping``) so it can never drift from what the converters declare;
# an extension none of them declares falls back to plain-text as well, which
# decides from the content instead of the name (see
# :func:`resolve_auto_pipeline`).
_AUTO_PRIORITY: tuple[ConversionPipeline, ...] = (
    ConversionPipeline.PLAIN_TEXT,
    ConversionPipeline.DOCLING,
    ConversionPipeline.PANDOC,
)


@cache
def _load_converter(spec: str) -> type[DocumentConverter]:
    """Import and return the converter class for a ``module:Class`` spec."""
    module_name, _, class_name = spec.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def _config_model(cls: type[DocumentConverter]) -> type[BaseModel] | None:
    """Derive a converter's Pydantic config model from its ``config`` field."""
    annotation = get_type_hints(cls).get("config")
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _available_converters() -> dict[ConversionPipeline, type[DocumentConverter]]:
    """Return converters whose modules and dependencies can be imported."""
    result: dict[ConversionPipeline, type[DocumentConverter]] = {}
    for pipeline, spec in _CONVERTERS.items():
        try:
            result[pipeline] = _load_converter(spec)
        except ImportError:
            continue
    return result


@cache
def _auto_mapping() -> dict[str, ConversionPipeline]:
    """Map file extensions to pipelines, preferring docling over pandoc.

    Built from each available converter's declared ``extensions`` in
    :data:`_AUTO_PRIORITY` order: docling claims every format it handles,
    pandoc fills the gaps, and unavailable converters are skipped (so if
    docling is not installed, pandoc transparently takes over its formats).
    """
    mapping: dict[str, ConversionPipeline] = {}
    for pipeline in _AUTO_PRIORITY:
        try:
            cls = _load_converter(_CONVERTERS[pipeline])
        except ImportError:
            continue
        for ext in cls.extensions:
            mapping.setdefault(ext, pipeline)
    return mapping


def resolve_auto_pipeline(filename: str) -> ConversionPipeline:
    """Resolve the AUTO pipeline to a concrete pipeline based on file extension.

    Falls back to :attr:`ConversionPipeline.PLAIN_TEXT` for an extension no
    converter declares, such as a source file, unusual config format, or name
    with no suffix.  AUTO preparation then decides from the content whether to
    index it as plain text or create a binary stub.

    Args:
        filename: The document filename.

    Returns:
        The resolved conversion pipeline.
    """
    return _auto_mapping().get(
        Path(filename).suffix.lower(), ConversionPipeline.PLAIN_TEXT
    )


class EntryProjection(StrEnum):
    """How an entry's markdown is derived from the file it is derived from."""

    MARKDOWN = "markdown"
    """The file is its own description; nothing is derived."""

    IMAGE = "image"
    VIDEO = "video"
    """Derived by a vision model, from the still or from sampled frames."""

    CONVERTIBLE = "convertible"
    """Derived by a converter, or copied verbatim when that converter is plain text."""


def projection_for(filename: str) -> EntryProjection:
    """Return which projection *filename* gets, by extension.

    The single routing table behind every path that turns a file into an entry:
    :func:`hivegent.workspace.prepare._prepare_upload` dispatches on it,
    reconciliation asks it which files it can derive a description for, and the
    metadata reconstruction asks it what kind of entry a rediscovered file
    belongs to.  Keeping the three on one table is what stops them from
    answering differently for the same file.
    """
    suffix = Path(filename).suffix.lower()
    if is_markdown_suffix(suffix):
        return EntryProjection.MARKDOWN
    if is_image_suffix(suffix):
        return EntryProjection.IMAGE
    if is_video_suffix(suffix):
        return EntryProjection.VIDEO
    return EntryProjection.CONVERTIBLE


_INGESTIBLE_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
"""Image formats a chat model accepts verbatim, the subset vision APIs agree on."""

INGESTIBLE_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    IMAGE_MEDIA_TYPES[ext] for ext in _INGESTIBLE_IMAGE_EXTENSIONS
)
"""Media types a chat model ingests verbatim as an image.

The one table behind both halves of the chat-attachment gate: the client renders
it as the file picker's ``accept`` filter (served in ``AttachmentLimits``) and
the chat route validates every attachment against it, so a file the model could
not read is refused in the browser and never costs a round trip.
"""

VISION_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    **{ext: IMAGE_MEDIA_TYPES[ext] for ext in _INGESTIBLE_IMAGE_EXTENSIONS},
    **VIDEO_MEDIA_TYPES,
}
"""Extension → media type for the formats a vision model can be shown.

Deliberately *not* ``IMAGE_MEDIA_TYPES | VIDEO_MEDIA_TYPES``, because the two
halves qualify for different reasons.  Video qualifies whatever the container,
since it is never sent as one: it is always sampled to PNG frames first, so
anything decodable is showable.  An image is sent verbatim, so only the formats
a chat model actually ingests belong — widening this to every format Pillow can
open would admit SVG, BMP, TIFF, and ICO, which the serving gateway rejects, and
would additionally make SVG unreadable as the text it is (both readers and the
write gateway refuse a file on this table alone).

The narrowness would stop being load-bearing if the binary reader rasterised
non-ingestible images the way it already samples video; that is a feature, not a
simplification of this table.
"""


TABULAR_SUFFIXES: frozenset[str] = frozenset(
    {".csv", ".tsv", ".parquet", ".xlsx", ".xlsb", ".xls"}
)
"""Extensions a SQL query can be run against in place.

The one table behind the read/query split, for the same reason
:data:`VISION_MEDIA_TYPES` is the one table behind read/read_binary: the tool
that queries these and the tool that would otherwise read them line by line
have to agree on which files they are, or the reader silently spends the
context on a table that could have been queried.

Wider than any single converter's ``extensions``, because a projection is not
the point: a columnar format no converter claims is still queryable, and a
format that does earn a markdown projection is still better queried.
"""


def is_tabular(file_path: str) -> bool:
    """Return whether *file_path* names a table a SQL query can be run against."""
    return Path(file_path).suffix.lower() in TABULAR_SUFFIXES


def vision_media_type(file_path: str) -> str | None:
    """Return *file_path*'s media type if a vision model can be shown it.

    The one table behind both halves of the read/write symmetry: a file named
    for one of these formats is what ``read_binary_document`` accepts and what
    the text tools refuse, so neither side can start claiming a file is text
    while the other calls it binary.
    """
    return VISION_MEDIA_TYPES.get(Path(file_path).suffix.lower())


def projects_verbatim(filename: str) -> bool:
    """Return whether *filename*'s projection is a copy of its own text.

    True for the files AUTO reads as-is — config, data-serialization, and source
    formats — for which deriving the projection costs a decode and a fenced
    block rather than a converter or a vision model.

    >>> projects_verbatim("settings.ini")
    True
    >>> projects_verbatim("diagram.svg")
    False
    """
    return (
        projection_for(filename) is EntryProjection.CONVERTIBLE
        and resolve_auto_pipeline(filename) is ConversionPipeline.PLAIN_TEXT
    )


def get_converter(
    pipeline: ConversionPipeline,
    filename: str = "",
    config: dict[str, Any] | None = None,
    llm_options: _LlmOptions | None = None,
    detect_asset_roles: bool = False,
) -> DocumentConverter:
    """Get a converter instance for the specified pipeline.

    Resolves AUTO to a concrete pipeline, validates extension compatibility,
    and returns a new converter instance with parsed config.

    Args:
        pipeline: The conversion pipeline to use.
        filename: The document filename (required when pipeline is AUTO).
        config: Optional raw config dict to parse into the pipeline's config model.
        llm_options: LLM provider options (only used for the LLM pipeline).
        detect_asset_roles: Whether to compute asset-role signals for
            extracted assets. Converters may skip the work of producing
            them (e.g. docling's picture classifier) when ``False``.

    Raises:
        ImportError: If the converter's dependencies are not installed.
        ValidationError: If the config is invalid for the pipeline.
        ValueError: If the pipeline is not recognized or the file extension
            is not supported by the chosen pipeline.
    """
    if pipeline == ConversionPipeline.AUTO:
        pipeline = resolve_auto_pipeline(filename)

    spec = _CONVERTERS.get(pipeline)
    if spec is None:
        raise ValueError(f"Unknown conversion pipeline: {pipeline}")
    try:
        cls = _load_converter(spec)
    except ImportError as exc:
        raise ImportError(
            f"Conversion pipeline '{pipeline.value}' is not available. "
            "Install its dependencies to enable it."
        ) from exc

    suffix = Path(filename).suffix.lower()
    if (
        suffix
        and cls.extensions
        and not cls.accepts_any_extension
        and suffix not in cls.extensions
    ):
        raise ValueError(
            f"Conversion pipeline '{pipeline.value}' does not support "
            f"{suffix}. Supported: {', '.join(sorted(cls.extensions))}"
        )

    kwargs: dict[str, Any] = {}
    if config and (model := _config_model(cls)) is not None:
        kwargs["config"] = model(**config)
    if pipeline == ConversionPipeline.LLM and llm_options is not None:
        kwargs["llm_options"] = llm_options

    return cls(detect_asset_roles=detect_asset_roles, **kwargs)


def get_conversion_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    available = _available_converters()
    all_extensions = sorted(
        {ext for cls in available.values() for ext in cls.extensions}
    )
    infos = [
        ConversionPipelineInfo(
            value=ConversionPipeline.AUTO.value,
            label="Auto",
            description="Automatically selects the best pipeline for each file",
            extensions=all_extensions,
        ),
    ]
    for pipeline, cls in available.items():
        model = _config_model(cls)
        infos.append(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=cls.label,
                description=cls.description,
                extensions=sorted(cls.extensions),
                config_schema=model.model_json_schema() if model else {},
                config_defaults=model().model_dump() if model else {},
            )
        )
    return infos
