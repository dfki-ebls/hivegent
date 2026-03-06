"""Document conversion infrastructure for Hivegent."""

import importlib
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .base import ConversionResult, DocumentConverter

__all__ = [
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "ConversionResult",
    "ConversionSpec",
    "DocumentConverter",
    "get_converter",
    "get_conversion_pipelines_info",
    "resolve_auto_pipeline",
]

logger = logging.getLogger(__name__)


class ConversionPipeline(StrEnum):
    """Available conversion pipelines."""

    AUTO = "auto"
    LLM = "llm"
    MARKER = "marker"
    DOCLING = "docling"
    MINERU = "mineru"
    PANDOC = "pandoc"
    MARKITDOWN = "markitdown"
    KREUZBERG = "kreuzberg"
    PDF_OXIDE = "pdf-oxide"
    TABLE_CHEF = "table-chef"
    TEXT_CHEF = "text-chef"


class ConversionSpec(BaseModel):
    """Conversion pipeline selection and configuration."""

    pipeline: ConversionPipeline = ConversionPipeline.AUTO
    config: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class _ConverterEntry:
    """Registry entry mapping a pipeline to its converter class and metadata."""

    module_name: str
    converter_class_name: str
    label: str
    description: str
    config_model_name: str | None = None


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


# Core converters (always available)
_CONVERTER_CONFIG: dict[ConversionPipeline, _ConverterEntry] = {
    ConversionPipeline.LLM: _ConverterEntry(
        module_name="hivegent.converters.llm",
        converter_class_name="LLMConverter",
        label="LLM",
        description="Uses vision model for all files",
        config_model_name="LlmConverterConfig",
    ),
    ConversionPipeline.PANDOC: _ConverterEntry(
        module_name="hivegent.converters.pandoc",
        converter_class_name="PandocConverter",
        label="Pandoc",
        description="Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
        "DocBook, Typst, and more",
        config_model_name="PandocConverterConfig",
    ),
}
_CONVERTER_CONFIG[ConversionPipeline.MARKER] = _ConverterEntry(
    module_name="hivegent.converters.marker",
    converter_class_name="MarkerConverter",
    label="Marker",
    description="Best for PDF documents",
    config_model_name="MarkerConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.DOCLING] = _ConverterEntry(
    module_name="hivegent.converters.docling",
    converter_class_name="DoclingConverter",
    label="Docling",
    description="Best for Office documents",
    config_model_name="DoclingConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.MINERU] = _ConverterEntry(
    module_name="hivegent.converters.mineru",
    converter_class_name="MinerUConverter",
    label="MinerU",
    description="High-quality PDF parsing (no XLSX)",
    config_model_name="MinerUConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.MARKITDOWN] = _ConverterEntry(
    module_name="hivegent.converters.markitdown",
    converter_class_name="MarkItDownConverter",
    label="MarkItDown",
    description="Microsoft's converter for Office, PDF, images, and more",
    config_model_name="MarkItDownConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.KREUZBERG] = _ConverterEntry(
    module_name="hivegent.converters.kreuzberg",
    converter_class_name="KreuzbergConverter",
    label="Kreuzberg",
    description="Text extraction from 75+ formats with OCR support",
    config_model_name="KreuzbergConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.PDF_OXIDE] = _ConverterEntry(
    module_name="hivegent.converters.pdf_oxide",
    converter_class_name="PdfOxideConverter",
    label="pdf_oxide",
    description="High-performance Rust-based PDF to markdown converter",
    config_model_name="PdfOxideConverterConfig",
)

_CONVERTER_CONFIG[ConversionPipeline.TABLE_CHEF] = _ConverterEntry(
    module_name="hivegent.converters.chonkie_table",
    converter_class_name="ChonkieTableConverter",
    label="Table Chef",
    description="CSV/Excel to markdown tables via pandas",
)

_CONVERTER_CONFIG[ConversionPipeline.TEXT_CHEF] = _ConverterEntry(
    module_name="hivegent.converters.chonkie_text",
    converter_class_name="ChonkieTextConverter",
    label="Text Chef",
    description="Plain text files as-is",
)

_AUTO_MAPPING: dict[str, ConversionPipeline] = {
    # Text formats (converted to clean markdown via pandoc)
    ".txt": ConversionPipeline.PANDOC,
    ".html": ConversionPipeline.PANDOC,
    ".xml": ConversionPipeline.PANDOC,
    ".csv": ConversionPipeline.PANDOC,
    ".adoc": ConversionPipeline.PANDOC,
    # Pandoc-handled formats
    ".odt": ConversionPipeline.PANDOC,
    ".rst": ConversionPipeline.PANDOC,
    ".rtf": ConversionPipeline.PANDOC,
    ".epub": ConversionPipeline.PANDOC,
    ".tex": ConversionPipeline.PANDOC,
    ".org": ConversionPipeline.PANDOC,
    ".docbook": ConversionPipeline.PANDOC,
    ".typst": ConversionPipeline.PANDOC,
    ".docx": ConversionPipeline.PANDOC,
    ".pptx": ConversionPipeline.PANDOC,
    ".xlsx": ConversionPipeline.PANDOC,
    ".fb2": ConversionPipeline.PANDOC,
    ".opml": ConversionPipeline.PANDOC,
    ".bib": ConversionPipeline.PANDOC,
    ".ris": ConversionPipeline.PANDOC,
    ".tsv": ConversionPipeline.PANDOC,
    ".ipynb": ConversionPipeline.PANDOC,
    ".textile": ConversionPipeline.PANDOC,
    ".creole": ConversionPipeline.PANDOC,
    ".djot": ConversionPipeline.PANDOC,
    ".dokuwiki": ConversionPipeline.PANDOC,
    ".mediawiki": ConversionPipeline.PANDOC,
    ".tikiwiki": ConversionPipeline.PANDOC,
    ".twiki": ConversionPipeline.PANDOC,
    ".vimwiki": ConversionPipeline.PANDOC,
    ".jira": ConversionPipeline.PANDOC,
    ".muse": ConversionPipeline.PANDOC,
    ".t2t": ConversionPipeline.PANDOC,
    ".jats": ConversionPipeline.PANDOC,
    ".man": ConversionPipeline.PANDOC,
    ".pod": ConversionPipeline.PANDOC,
    # Docling-handled formats
    ".pdf": ConversionPipeline.DOCLING,
    ".png": ConversionPipeline.DOCLING,
    ".jpg": ConversionPipeline.DOCLING,
    ".jpeg": ConversionPipeline.DOCLING,
}


@lru_cache(maxsize=None)
def _load_module_attr(module_name: str, attr_name: str) -> Any:
    """Import and return an attribute from a converter module."""
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _get_converter_class(entry: _ConverterEntry) -> type[DocumentConverter]:
    """Load the converter class for a registry entry."""
    converter_class = _load_module_attr(entry.module_name, entry.converter_class_name)
    return converter_class


def _get_config_model(entry: _ConverterEntry) -> type[BaseModel] | None:
    """Load the config model for a registry entry when defined."""
    if entry.config_model_name is None:
        return None
    return _load_module_attr(entry.module_name, entry.config_model_name)


def _get_available_converter_classes() -> dict[ConversionPipeline, type[DocumentConverter]]:
    """Return registry entries whose converter classes can be imported."""
    available: dict[ConversionPipeline, type[DocumentConverter]] = {}
    for pipeline, entry in _CONVERTER_CONFIG.items():
        try:
            available[pipeline] = _get_converter_class(entry)
        except ImportError:
            continue
    return available


def resolve_auto_pipeline(filename: str) -> ConversionPipeline:
    """Resolve the AUTO pipeline to a concrete pipeline based on file extension.

    Falls back to :attr:`ConversionPipeline.LLM` for extensions without an
    explicit mapping (e.g. unknown binary formats sent to a vision model).

    Args:
        filename: The document filename.

    Returns:
        The resolved conversion pipeline.
    """
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return _AUTO_MAPPING.get(suffix, ConversionPipeline.LLM)


def get_converter(
    pipeline: ConversionPipeline,
    filename: str = "",
    config: dict[str, Any] | None = None,
    llm_options: _LlmOptions | None = None,
) -> DocumentConverter:
    """Get a converter instance for the specified pipeline.

    Resolves AUTO to a concrete pipeline, validates extension compatibility,
    and returns a new converter instance with parsed config.

    Args:
        pipeline: The conversion pipeline to use.
        filename: The document filename (required when pipeline is AUTO).
        config: Optional raw config dict to parse into the pipeline's config model.
        llm_options: LLM provider options (only used for the LLM pipeline).

    Raises:
        ImportError: If the converter's dependencies are not installed.
        ValidationError: If the config is invalid for the pipeline.
        ValueError: If the pipeline is not recognized or the file extension
            is not supported by the chosen pipeline.
    """
    if pipeline == ConversionPipeline.AUTO:
        pipeline = resolve_auto_pipeline(filename)

    if pipeline not in _CONVERTER_CONFIG:
        if pipeline in ConversionPipeline:
            raise ImportError(
                f"Conversion pipeline '{pipeline.value}' is not available. "
                f"Install its dependencies to enable it."
            )
        raise ValueError(f"Unknown conversion pipeline: {pipeline}")

    entry = _CONVERTER_CONFIG[pipeline]
    try:
        converter_class = _get_converter_class(entry)
    except ImportError as exc:
        raise ImportError(
            f"Conversion pipeline '{pipeline.value}' is not available. "
            "Install its dependencies to enable it."
        ) from exc
    extensions = converter_class.extensions

    if filename:
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if suffix and extensions and suffix not in extensions:
            raise ValueError(
                f"Conversion pipeline '{pipeline.value}' does not support "
                f"{suffix}. Supported: {', '.join(sorted(extensions))}"
            )

    kwargs: dict[str, Any] = {}
    if config:
        config_model = _get_config_model(entry)
        if config_model is not None:
            kwargs["config"] = config_model(**config)
    if pipeline == ConversionPipeline.LLM and llm_options is not None:
        kwargs["llm_options"] = llm_options

    return converter_class(**kwargs)


def get_conversion_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    available = _get_available_converter_classes()
    all_extensions = sorted(
        {
            ext
            for converter_class in available.values()
            for ext in converter_class.extensions
        }
    )
    infos = [
        ConversionPipelineInfo(
            value=ConversionPipeline.AUTO.value,
            label="Auto",
            description="Automatically selects the best pipeline for each file",
            extensions=all_extensions,
        ),
    ]
    for pipeline, converter_class in available.items():
        entry = _CONVERTER_CONFIG[pipeline]
        config_schema: dict[str, Any] = {}
        config_defaults: dict[str, Any] = {}
        config_model = _get_config_model(entry)
        if config_model is not None:
            config_schema = config_model.model_json_schema()
            try:
                config_defaults = config_model().model_dump()
            except ValidationError:
                logger.warning(
                    "Config model %s is not default-constructible",
                    config_model.__name__,
                )
        infos.append(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=entry.label,
                description=entry.description,
                extensions=sorted(converter_class.extensions),
                config_schema=config_schema,
                config_defaults=config_defaults,
            )
        )
    return infos
