"""Document conversion infrastructure for Hivegent."""

import importlib
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Protocol, get_type_hints

from pydantic import BaseModel

from .base import ConversionResult, DocumentConverter

__all__ = [
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "ConversionResult",
    "ConversionSpec",
    "DocumentConverter",
    "get_conversion_pipelines_info",
    "get_converter",
    "resolve_auto_pipeline",
]


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
    ConversionPipeline.PDF_OXIDE: "hivegent.converters.pdf_oxide:PdfOxideConverter",
    ConversionPipeline.TABLE_CHEF: "hivegent.converters.chonkie_table:ChonkieTableConverter",
    ConversionPipeline.TEXT_CHEF: "hivegent.converters.chonkie_text:ChonkieTextConverter",
}


# AUTO routing preference: docling claims every format it can handle, pandoc
# covers the rest, and anything neither supports falls back to the LLM pipeline.
# Routing is derived from each converter's own ``extensions`` (see
# ``_auto_mapping``) so it can never drift from what the converters declare.
_AUTO_PRIORITY: tuple[ConversionPipeline, ...] = (
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

    Falls back to :attr:`ConversionPipeline.LLM` for extensions supported by
    neither docling nor pandoc (e.g. unknown binary formats sent to a vision
    model).

    Args:
        filename: The document filename.

    Returns:
        The resolved conversion pipeline.
    """
    return _auto_mapping().get(Path(filename).suffix.lower(), ConversionPipeline.LLM)


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
    if suffix and cls.extensions and suffix not in cls.extensions:
        raise ValueError(
            f"Conversion pipeline '{pipeline.value}' does not support "
            f"{suffix}. Supported: {', '.join(sorted(cls.extensions))}"
        )

    kwargs: dict[str, Any] = {}
    if config and (model := _config_model(cls)) is not None:
        kwargs["config"] = model(**config)
    if pipeline == ConversionPipeline.LLM and llm_options is not None:
        kwargs["llm_options"] = llm_options

    return cls(**kwargs)


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
