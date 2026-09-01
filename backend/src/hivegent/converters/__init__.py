"""Document conversion infrastructure for Hivegent."""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..llm_config import LlmConfig
from ..pipeline_registry import (
    PipelineConfigInfo,
    PipelineImplementation,
    PipelineRegistration,
)
from .base import (
    IMAGE_MEDIA_TYPES,
    ConversionResult,
    DocumentConverter,
    is_image_suffix,
    is_markdown_suffix,
)
from .formats import (
    DOCLING_EXTENSIONS,
    LLM_MEDIA_TYPES,
    PANDOC_EXTENSIONS,
    PANDOC_SANDBOX_INCOMPATIBLE,
    PLAIN_TEXT_EXTENSIONS,
    match_file_extension,
)
from .video import VIDEO_MEDIA_TYPES, is_video_suffix

__all__ = [
    "BINARY_SUFFIXES",
    "BINARY_WRITE_REASON",
    "DELIMITED_SUFFIXES",
    "DELIMITERS",
    "INGESTIBLE_IMAGE_MEDIA_TYPES",
    "JSON_SUFFIXES",
    "TABULAR_SUFFIXES",
    "VISION_MEDIA_TYPES",
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "ConversionResult",
    "ConversionSpec",
    "DocumentConverter",
    "EntryProjection",
    "get_conversion_pipeline_config",
    "get_conversion_pipelines_info",
    "get_converter",
    "is_json",
    "is_tabular",
    "projection_for",
    "projects_verbatim",
    "resolve_auto_pipeline",
    "vision_media_type",
    "writes_as_text",
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


@dataclass(slots=True, frozen=True)
class _ConverterRegistration(PipelineRegistration[DocumentConverter]):
    """Static converter metadata used without importing its implementation."""

    extensions: frozenset[str] | None
    """Extensions the converter accepts, or ``None`` when it accepts any file.

    A hard capability: :func:`get_converter` refuses a file outside it.
    """

    auto_extensions: frozenset[str] = frozenset()
    """Extensions AUTO prefers this converter for; set only on `_AUTO_PRIORITY`.

    Kept apart from :attr:`extensions` because the two answer different
    questions.  Plain text accepts every file but should only *win* AUTO for
    the raw-text formats a richer converter would otherwise claim.
    """

    @property
    def advertised_extensions(self) -> frozenset[str]:
        """Extensions the API lists this pipeline under."""
        return self.extensions if self.extensions is not None else self.auto_extensions


def _load_llm() -> PipelineImplementation[DocumentConverter]:
    from .llm import LLMConverter, LlmConverterConfig

    return PipelineImplementation(LLMConverter, LlmConverterConfig)


def _load_pandoc() -> PipelineImplementation[DocumentConverter]:
    from .pandoc import PandocConverter, PandocConverterConfig

    return PipelineImplementation(PandocConverter, PandocConverterConfig)


def _load_marker() -> PipelineImplementation[DocumentConverter]:
    from .marker import MarkerConverter, MarkerConverterConfig

    return PipelineImplementation(MarkerConverter, MarkerConverterConfig)


def _load_docling() -> PipelineImplementation[DocumentConverter]:
    from .docling import DoclingConverter, DoclingConverterConfig

    return PipelineImplementation(DoclingConverter, DoclingConverterConfig)


def _load_mineru() -> PipelineImplementation[DocumentConverter]:
    from .mineru import MinerUConverter, MinerUConverterConfig

    return PipelineImplementation(MinerUConverter, MinerUConverterConfig)


def _load_markitdown() -> PipelineImplementation[DocumentConverter]:
    from .markitdown import MarkItDownConverter, MarkItDownConverterConfig

    return PipelineImplementation(MarkItDownConverter, MarkItDownConverterConfig)


def _load_kreuzberg() -> PipelineImplementation[DocumentConverter]:
    from .kreuzberg import KreuzbergConverter, KreuzbergConverterConfig

    return PipelineImplementation(KreuzbergConverter, KreuzbergConverterConfig)


def _load_anydoc() -> PipelineImplementation[DocumentConverter]:
    from .anydoc import AnydocConverter

    return PipelineImplementation(AnydocConverter)


def _load_pdf_inspector() -> PipelineImplementation[DocumentConverter]:
    from .pdf_inspector import PdfInspectorConverter, PdfInspectorConverterConfig

    return PipelineImplementation(PdfInspectorConverter, PdfInspectorConverterConfig)


def _load_pdf_oxide() -> PipelineImplementation[DocumentConverter]:
    from .pdf_oxide import PdfOxideConverter, PdfOxideConverterConfig

    return PipelineImplementation(PdfOxideConverter, PdfOxideConverterConfig)


def _load_table_chef() -> PipelineImplementation[DocumentConverter]:
    from .chonkie_table import ChonkieTableConverter

    return PipelineImplementation(ChonkieTableConverter)


def _load_plain_text() -> PipelineImplementation[DocumentConverter]:
    from .plain_text import PlainTextConverter

    return PipelineImplementation(PlainTextConverter)


# Static metadata keeps routing dependency-free. Implementations load only when
# selected for conversion or inspected for their configuration schema.
_CONVERTERS: dict[ConversionPipeline, _ConverterRegistration] = {
    ConversionPipeline.LLM: _ConverterRegistration(
        loader=_load_llm,
        label="LLM",
        description="Uses vision model for all files",
        extensions=frozenset(LLM_MEDIA_TYPES),
    ),
    ConversionPipeline.PANDOC: _ConverterRegistration(
        loader=_load_pandoc,
        label="Pandoc",
        description=(
            "Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
            "DocBook, Typst, and more"
        ),
        extensions=PANDOC_EXTENSIONS,
        auto_extensions=PANDOC_EXTENSIONS,
    ),
    # Marker only converts PDFs. The provider registry lives in
    # marker.providers.registry but has no public format listing API.
    # https://github.com/VikParuchuri/marker
    ConversionPipeline.MARKER: _ConverterRegistration(
        loader=_load_marker,
        label="Marker",
        description="Best for PDF documents",
        extensions=frozenset({".pdf"}),
        dependencies=("marker",),
    ),
    ConversionPipeline.DOCLING: _ConverterRegistration(
        loader=_load_docling,
        label="Docling",
        description="Best for Office documents",
        extensions=DOCLING_EXTENSIONS,
        auto_extensions=DOCLING_EXTENSIONS,
        dependencies=("docling", "tesserocr"),
    ),
    # MinerU has no public format listing API.
    # https://github.com/opendatalab/MinerU#supported-file-types
    ConversionPipeline.MINERU: _ConverterRegistration(
        loader=_load_mineru,
        label="MinerU",
        description="High-quality PDF parsing (no XLSX)",
        extensions=frozenset({".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg"}),
        dependencies=("mineru",),
    ),
    # MarkItDown has no public format listing API. Each converter in
    # markitdown.converters defines its own ACCEPTED_FILE_EXTENSIONS constant.
    # https://github.com/microsoft/markitdown/tree/main/packages/markitdown/src/markitdown/converters
    ConversionPipeline.MARKITDOWN: _ConverterRegistration(
        loader=_load_markitdown,
        label="MarkItDown",
        description="Microsoft's converter for Office, PDF, images, and more",
        extensions=frozenset(
            {
                ".pdf",
                ".docx",
                ".xlsx",
                ".xls",
                ".pptx",
                ".html",
                ".htm",
                ".csv",
                ".json",
                ".jsonl",
                ".ndjson",
                ".xml",
                ".rss",
                ".atom",
                ".epub",
                ".ipynb",
                ".zip",
                ".txt",
                ".md",
                ".png",
                ".jpg",
                ".jpeg",
                ".wav",
                ".mp3",
                ".m4a",
                ".msg",
            }
        ),
        dependencies=("markitdown",),
    ),
    # Kreuzberg exposes get_extensions_for_mime() per MIME type but has no
    # API to enumerate all supported types at once.
    # https://docs.kreuzberg.dev/features/supported-formats/
    ConversionPipeline.KREUZBERG: _ConverterRegistration(
        loader=_load_kreuzberg,
        label="Kreuzberg",
        description="Text extraction from 75+ formats with OCR support",
        extensions=frozenset(
            {
                ".pdf",
                ".docx",
                ".xlsx",
                ".pptx",
                ".doc",
                ".xls",
                ".ppt",
                ".odt",
                ".ods",
                ".html",
                ".htm",
                ".xml",
                ".json",
                ".csv",
                ".epub",
                ".rtf",
                ".txt",
                ".md",
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".tiff",
                ".tif",
                ".bmp",
                ".svg",
                ".ico",
                ".msg",
                ".eml",
                ".zip",
                ".tar",
                ".gz",
                ".7z",
            }
        ),
        dependencies=("kreuzberg",),
    ),
    # https://github.com/firecrawl/anydoc#supported-formats, minus ``.pdf``
    # (see AnydocConverter's docstring for why).
    ConversionPipeline.ANYDOC: _ConverterRegistration(
        loader=_load_anydoc,
        label="anydoc",
        description=(
            "Fast structural converter for Office, OpenDocument, RTF, EPUB, and CSV"
        ),
        extensions=frozenset(
            {
                ".doc",
                ".docx",
                ".docm",
                ".ppt",
                ".pps",
                ".pot",
                ".pptx",
                ".pptm",
                ".ppsx",
                ".ppsm",
                ".xls",
                ".xlsx",
                ".xlsm",
                ".xlsb",
                ".odt",
                ".ods",
                ".odp",
                ".rtf",
                ".epub",
                ".csv",
            }
        ),
    ),
    ConversionPipeline.PDF_INSPECTOR: _ConverterRegistration(
        loader=_load_pdf_inspector,
        label="pdf-inspector",
        description="Fast layout-aware PDF to markdown converter, no OCR",
        extensions=frozenset({".pdf"}),
    ),
    ConversionPipeline.PDF_OXIDE: _ConverterRegistration(
        loader=_load_pdf_oxide,
        label="pdf_oxide",
        description="High-performance Rust-based PDF to markdown converter",
        extensions=frozenset({".pdf"}),
        dependencies=("pdf_oxide",),
    ),
    ConversionPipeline.TABLE_CHEF: _ConverterRegistration(
        loader=_load_table_chef,
        label="Table Chef",
        description="CSV/Excel to markdown tables via pandas",
        extensions=frozenset({".csv", ".xls", ".xlsx"}),
    ),
    # A routing preference, not a capability: these overlap richer converters
    # and so need an explicit AUTO priority, while every other suffix reaches
    # plain text through AUTO's default.  That is what ``accepts_any_extension``
    # expresses, waiving the extension check so the converter decides from the
    # content.
    ConversionPipeline.PLAIN_TEXT: _ConverterRegistration(
        loader=_load_plain_text,
        label="Plain Text",
        description="Text, configuration, data-serialization, and source files as-is",
        extensions=None,
        auto_extensions=PLAIN_TEXT_EXTENSIONS,
    ),
}


_AUTO_PRIORITY: tuple[ConversionPipeline, ...] = (
    ConversionPipeline.PLAIN_TEXT,
    ConversionPipeline.DOCLING,
    ConversionPipeline.PANDOC,
)
_AUTO_EXTENSIONS = frozenset(
    extension
    for pipeline in _AUTO_PRIORITY
    for extension in _CONVERTERS[pipeline].auto_extensions
)


def _auto_candidates(filename: str) -> tuple[ConversionPipeline, ...]:
    """Return deterministic AUTO candidates in preference order.

    Filtered on declared dependencies so ``resolve_auto_pipeline`` never names a
    pipeline :func:`get_converter` would skip; the ``ImportError`` loop there
    stays as a backstop for a dependency that imports but fails.
    """
    extension = match_file_extension(filename, _AUTO_EXTENSIONS)
    candidates = tuple(
        pipeline
        for pipeline in _AUTO_PRIORITY
        if extension in _CONVERTERS[pipeline].auto_extensions
        and _CONVERTERS[pipeline].available
    )
    if ConversionPipeline.PLAIN_TEXT in candidates:
        return candidates

    return (*candidates, ConversionPipeline.PLAIN_TEXT)


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
    return _auto_candidates(filename)[0]


class EntryProjection(StrEnum):
    """How an entry's markdown is derived from the file it is derived from."""

    MARKDOWN = "markdown"
    """The file is its own description; nothing is derived."""

    IMAGE = "image"
    VIDEO = "video"
    """Derived by a vision model, from the still or from sampled frames."""

    CONVERTIBLE = "convertible"
    """Derived by a converter, or copied verbatim when that converter is plain text."""


_TEXT_IMAGE_SUFFIXES: frozenset[str] = frozenset({".svg"})
"""Image formats that are markup, so a text write does create one.

The exemption :data:`IMAGE_MEDIA_TYPES` needs before it can stand for "not
text": SVG is the one entry there that a reader already serves as the text it
is (:data:`VISION_MEDIA_TYPES` leaves it out for exactly that reason), so the
write gate has to admit it or the two would disagree about the same file.
"""


def projection_for(filename: str) -> EntryProjection:
    """Return which projection *filename* gets, by extension.

    The single routing table behind every path that turns a file into an entry:
    :func:`hivegent.workspace.prepare._prepare_upload` dispatches on it,
    reconciliation asks it which files it can derive a description for, and the
    metadata reconstruction asks it what kind of entry a rediscovered file
    belongs to.  Keeping the three on one table is what stops them from
    answering differently for the same file.

    An image whose format is markup is not one of them: a vision model cannot
    be shown an SVG (:data:`VISION_MEDIA_TYPES` says so), so routing it there
    spends a request to describe a file whose own text is the better index.

    >>> projection_for("diagram.svg") is EntryProjection.CONVERTIBLE
    True
    >>> projection_for("photo.png") is EntryProjection.IMAGE
    True
    """
    suffix = Path(filename).suffix.lower()
    if is_markdown_suffix(suffix):
        return EntryProjection.MARKDOWN
    if is_image_suffix(suffix) and suffix not in _TEXT_IMAGE_SUFFIXES:
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

DELIMITERS: dict[str, str] = {".csv": ",", ".tsv": "\t"}
"""What separates the fields of each delimited format, by its suffix.

The suffix decides it and nothing else does: a `.csv` is comma-separated by
the name it was given, so a semicolon export saved under that name is one
column and reads as one, rather than being guessed at by one surface and not
the other.  Sniffing was the alternative and is worse twice over — it needs
the very row-width consistency the write gate is checking for, and a reader
and a writer sniffing separately can disagree about the same file.
"""

DELIMITED_SUFFIXES: frozenset[str] = frozenset(DELIMITERS)
"""The text half of :data:`TABULAR_SUFFIXES`.

Three tools split on it and must not disagree: the loader retries one of these
through the shared text decoder when it is not UTF-8, the write gate lets one
be created as the text it is while the columnar rest cannot be, and the same
gate checks its rows against its header.
"""


JSON_SUFFIXES: frozenset[str] = frozenset({".json"})
"""Extensions a jq filter can be run against in place.

The third table of its kind, and there for the same reason as
:data:`TABULAR_SUFFIXES`: a document whose answer is one field of one record
costs the whole file to read and nothing to filter, so the tool that filters
these and the reader that would otherwise page through them have to agree on
which files they are.

Narrower than "text that happens to parse as JSON", deliberately: jq is handed
one document, so a line-delimited ``.jsonl`` would fail on its second line, and
a ``.json`` suffix is the only thing that promises a single value.
"""


BINARY_SUFFIXES: frozenset[str] = (
    (frozenset(IMAGE_MEDIA_TYPES) - _TEXT_IMAGE_SUFFIXES)
    | frozenset(VIDEO_MEDIA_TYPES)
    | frozenset(LLM_MEDIA_TYPES)
    | PANDOC_SANDBOX_INCOMPATIBLE
    | (TABULAR_SUFFIXES - DELIMITED_SUFFIXES)
)
"""Extensions whose bytes are not text.

The fourth table of its kind, and the one behind the write gate the way
:data:`VISION_MEDIA_TYPES` is the one behind read/read_binary: a text write
produces UTF-8, so it can create anything whose format *is* text and nothing
whose format is a container the ingest would then open as bytes.

Composed only from the tables that already name a binary format — what a vision
model is shown, what is sent as bytes, what pandoc cannot read in its sandbox,
what a query reads columnar — rather than from a list of every binary format
there is.  A suffix no converter claims is not on it, and a text write does
create such a file: what lands is the text that was written, indexed as the
text it is, which is what the name promised as much as anything else could.
"""

BINARY_WRITE_REASON = (
    "is a binary format, which a text write cannot create: write the text to a "
    "'.md' path instead, or upload the file"
)
"""Shared wording for a rejected write, wrapped in each layer's own exception.

The counterpart of :data:`~hivegent.text.NOT_TEXT_REASON` on the read side, and
worded like it: a refusal that names an action the caller can take, since the
tool surfaces reach this one and a model has no upload of its own.
"""


def is_tabular(file_path: str) -> bool:
    """Return whether *file_path* names a table a SQL query can be run against."""
    return Path(file_path).suffix.lower() in TABULAR_SUFFIXES


def is_json(file_path: str) -> bool:
    """Return whether *file_path* names a document a jq filter can be run against."""
    return Path(file_path).suffix.lower() in JSON_SUFFIXES


def vision_media_type(file_path: str) -> str | None:
    """Return *file_path*'s media type if a vision model can be shown it.

    The one table behind both halves of the read/write symmetry: a file named
    for one of these formats is what ``read_binary_document`` accepts and what
    the text tools refuse, so neither side can start claiming a file is text
    while the other calls it binary.
    """
    return VISION_MEDIA_TYPES.get(Path(file_path).suffix.lower())


def writes_as_text(filename: str) -> bool:
    """Return whether *filename* can be created by writing text at it.

    The write gate, and the exact counterpart of the read one: a read decides
    from the bytes (:func:`~hivegent.text.decode_bytes`), which a file that
    does not exist yet has none of, so creation decides from the format's own
    table instead.  Deliberately *not* :func:`projects_verbatim`, which answers
    whether the ingest may derive a projection by copying the text: that is
    false for every text format a converter claims, so gating a write on it
    refused `.csv`, `.html`, and `.tex` while admitting `.parquet` and `.zip`.

    >>> writes_as_text("report.csv")
    True
    >>> writes_as_text("notes/report.docx")
    False
    >>> writes_as_text("figure.svg")
    True
    >>> writes_as_text("figure.png")
    False
    >>> writes_as_text("Makefile")
    True
    """
    return Path(filename).suffix.lower() not in BINARY_SUFFIXES


def projects_verbatim(filename: str) -> bool:
    """Return whether *filename*'s projection is a copy of its own text.

    True for the files AUTO reads as-is — config, data-serialization, and source
    formats — for which deriving the projection costs a decode and a fenced
    block rather than a converter or a vision model.

    >>> projects_verbatim("settings.ini")
    True
    >>> projects_verbatim("report.csv")
    False
    """
    return (
        projection_for(filename) is EntryProjection.CONVERTIBLE
        and resolve_auto_pipeline(filename) is ConversionPipeline.PLAIN_TEXT
    )


def _reject_unsupported_extension(
    pipeline: ConversionPipeline,
    registration: _ConverterRegistration,
    filename: str,
) -> None:
    """Refuse a file the named pipeline cannot read.

    Answered from the static metadata, so a mismatch costs nothing beyond the
    lookup: the backend is never imported for a file it would reject.
    """
    if registration.extensions is None:
        return

    extension = match_file_extension(filename, registration.extensions)
    if extension and extension not in registration.extensions:
        raise ValueError(
            f"Conversion pipeline '{pipeline.value}' does not support "
            f"{extension}. Supported: {', '.join(sorted(registration.extensions))}"
        )


def _instantiate(
    pipeline: ConversionPipeline,
    implementation: PipelineImplementation[DocumentConverter],
    config: dict[str, Any] | None,
    llm_options: LlmConfig | None,
    detect_asset_roles: bool,
) -> DocumentConverter:
    """Construct the loaded converter with its parsed config."""
    kwargs: dict[str, Any] = {}
    if config and implementation.config is not None:
        kwargs["config"] = implementation.config(**config)
    if pipeline is ConversionPipeline.LLM and llm_options is not None:
        kwargs["llm_options"] = llm_options

    return implementation.cls(detect_asset_roles=detect_asset_roles, **kwargs)


def get_converter(
    pipeline: ConversionPipeline,
    filename: str = "",
    config: dict[str, Any] | None = None,
    llm_options: LlmConfig | None = None,
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
    if pipeline is not ConversionPipeline.AUTO:
        registration = _CONVERTERS.get(pipeline)
        if registration is None:
            raise ValueError(f"Unknown conversion pipeline: {pipeline}")

        _reject_unsupported_extension(pipeline, registration, filename)
        try:
            implementation = registration.load(pipeline.value)
        except ImportError as exc:
            raise ImportError(
                f"Conversion pipeline '{pipeline.value}' is not available. "
                "Install its dependencies to enable it."
            ) from exc

        return _instantiate(
            pipeline, implementation, config, llm_options, detect_asset_roles
        )

    # AUTO candidates are already chosen by extension and declared availability,
    # so the only thing left to discover is a dependency that fails to import.
    unavailable: ImportError | None = None
    for candidate in _auto_candidates(filename):
        try:
            implementation = _CONVERTERS[candidate].load(candidate.value)
        except ImportError as exc:
            unavailable = exc
            continue

        return _instantiate(
            candidate, implementation, config, llm_options, detect_asset_roles
        )

    raise ImportError("No AUTO conversion pipeline is available") from unavailable


def get_conversion_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get dependency-free metadata for installed conversion pipelines."""
    available = {
        pipeline: registration
        for pipeline, registration in _CONVERTERS.items()
        if registration.available
    }
    all_extensions = sorted(
        {
            extension
            for registration in available.values()
            for extension in registration.advertised_extensions
        }
    )
    return [
        ConversionPipelineInfo(
            value=ConversionPipeline.AUTO.value,
            label="Auto",
            description="Automatically selects the best pipeline for each file",
            extensions=all_extensions,
        ),
        *(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=registration.label,
                description=registration.description,
                extensions=sorted(registration.advertised_extensions),
            )
            for pipeline, registration in available.items()
        ),
    ]


@cache
def get_conversion_pipeline_config(
    pipeline: ConversionPipeline,
) -> PipelineConfigInfo:
    """Get configuration metadata for one selected conversion pipeline."""
    registration = _CONVERTERS.get(pipeline)
    if registration is None or not registration.available:
        raise ValueError(f"Conversion pipeline '{pipeline.value}' is not available")

    return registration.config_info(pipeline.value)
