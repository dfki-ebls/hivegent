"""Document conversion infrastructure for Hivegent."""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from cbrkit.helpers import optional_dependencies

from .base import DocumentConverter
from .llm import LLMConverter, LlmConverterConfig
from .pandoc import PandocConverter, PandocConverterConfig

__all__ = [
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "ConversionSpec",
    "DocumentConverter",
    "get_converter",
    "get_pipelines_info",
    "resolve_auto_pipeline",
    "validate_conversion_config",
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


class ConversionSpec(BaseModel):
    """Conversion pipeline selection and configuration."""

    pipeline: ConversionPipeline = ConversionPipeline.AUTO
    config: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class _ConverterEntry:
    """Registry entry mapping a pipeline to its converter class and metadata."""

    converter_class: type[DocumentConverter]
    label: str
    description: str
    config_model: type[BaseModel] | None = None


@dataclass(slots=True, frozen=True)
class ConversionPipelineInfo:
    """Public metadata for a conversion pipeline."""

    value: str
    label: str
    description: str
    extensions: list[str]
    config_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)


# Core converters (always available)
_CONVERTER_CONFIG: dict[ConversionPipeline, _ConverterEntry] = {
    ConversionPipeline.LLM: _ConverterEntry(
        converter_class=LLMConverter,
        label="LLM",
        description="Uses vision model for all files",
        config_model=LlmConverterConfig,
    ),
    ConversionPipeline.PANDOC: _ConverterEntry(
        converter_class=PandocConverter,
        label="Pandoc",
        description="Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
        "DocBook, Typst, and more",
        config_model=PandocConverterConfig,
    ),
}

# Optional converters (registered only when their dependencies are installed)
with optional_dependencies():
    from .marker import MarkerConverter, MarkerConverterConfig

    _CONVERTER_CONFIG[ConversionPipeline.MARKER] = _ConverterEntry(
        converter_class=MarkerConverter,
        label="Marker",
        description="Best for PDF documents",
        config_model=MarkerConverterConfig,
    )

with optional_dependencies():
    from .docling import DoclingConverter, DoclingConverterConfig

    _CONVERTER_CONFIG[ConversionPipeline.DOCLING] = _ConverterEntry(
        converter_class=DoclingConverter,
        label="Docling",
        description="Best for Office documents",
        config_model=DoclingConverterConfig,
    )

with optional_dependencies():
    from .mineru import MinerUConverter, MinerUConverterConfig

    _CONVERTER_CONFIG[ConversionPipeline.MINERU] = _ConverterEntry(
        converter_class=MinerUConverter,
        label="MinerU",
        description="High-quality PDF parsing (no XLSX)",
        config_model=MinerUConverterConfig,
    )

with optional_dependencies():
    from .markitdown import MarkItDownConverter, MarkItDownConverterConfig

    _CONVERTER_CONFIG[ConversionPipeline.MARKITDOWN] = _ConverterEntry(
        converter_class=MarkItDownConverter,
        label="MarkItDown",
        description="Microsoft's converter for Office, PDF, images, and more",
        config_model=MarkItDownConverterConfig,
    )

with optional_dependencies():
    from .kreuzberg import KreuzbergConverter, KreuzbergConverterConfig

    _CONVERTER_CONFIG[ConversionPipeline.KREUZBERG] = _ConverterEntry(
        converter_class=KreuzbergConverter,
        label="Kreuzberg",
        description="Text extraction from 75+ formats with OCR support",
        config_model=KreuzbergConverterConfig,
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
    pipeline: ConversionPipeline, filename: str = ""
) -> DocumentConverter:
    """Get a converter instance for the specified pipeline.

    Resolves AUTO to a concrete pipeline, validates extension compatibility,
    and returns a new converter instance.

    Args:
        pipeline: The conversion pipeline to use.
        filename: The document filename (required when pipeline is AUTO).

    Raises:
        ImportError: If the converter's dependencies are not installed.
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
    extensions = entry.converter_class.extensions

    if filename:
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if suffix and extensions and suffix not in extensions:
            raise ValueError(
                f"Conversion pipeline '{pipeline.value}' does not support "
                f"{suffix}. Supported: {', '.join(sorted(extensions))}"
            )

    return entry.converter_class()


def validate_conversion_config(spec: ConversionSpec) -> dict[str, Any] | None:
    """Validate a conversion config dict against the pipeline's config model.

    For ``AUTO`` pipelines, validation is skipped since the concrete pipeline
    is not known until file extension resolution.

    Args:
        spec: The conversion spec containing pipeline and config.

    Returns:
        The validated and normalized config dict, or ``None`` if no config.

    Raises:
        ValidationError: If the config is invalid for the pipeline.
    """
    if spec.config is None or spec.pipeline == ConversionPipeline.AUTO:
        return spec.config
    entry = _CONVERTER_CONFIG.get(spec.pipeline)
    if entry is None or entry.config_model is None:
        return spec.config
    validated = entry.config_model(**spec.config)
    return validated.model_dump()


def get_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    all_extensions = sorted(
        {
            ext
            for e in _CONVERTER_CONFIG.values()
            for ext in e.converter_class.extensions
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
    for pipeline, entry in _CONVERTER_CONFIG.items():
        config_schema: dict[str, Any] = {}
        config_defaults: dict[str, Any] = {}
        if entry.config_model is not None:
            config_schema = entry.config_model.model_json_schema()
            try:
                config_defaults = entry.config_model().model_dump()
            except ValidationError:
                logger.warning(
                    "Config model %s is not default-constructible",
                    entry.config_model.__name__,
                )
        infos.append(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=entry.label,
                description=entry.description,
                extensions=sorted(entry.converter_class.extensions),
                config_schema=config_schema,
                config_defaults=config_defaults,
            )
        )
    return infos
