"""Document conversion infrastructure for SnipScout."""

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module

from .base import DocumentConverter

__all__ = [
    "ConversionPipeline",
    "ConversionPipelineInfo",
    "DocumentConverter",
    "get_converter",
    "get_pipelines_info",
    "resolve_auto_pipeline",
]


class ConversionPipeline(StrEnum):
    """Available conversion pipelines."""

    AUTO = "auto"
    LLM = "llm"
    MARKER = "marker"
    DOCLING = "docling"
    MINERU = "mineru"


@dataclass(frozen=True)
class _ConverterEntry:
    """Internal registry entry mapping a pipeline to its implementation."""

    module_name: str
    class_name: str


@dataclass(frozen=True)
class ConversionPipelineInfo:
    """Public metadata for a conversion pipeline."""

    value: str
    label: str
    description: str
    extensions: list[str]


_CONVERTER_CONFIG: dict[ConversionPipeline, _ConverterEntry] = {
    ConversionPipeline.LLM: _ConverterEntry("llm_converter", "LLMConverter"),
    ConversionPipeline.MARKER: _ConverterEntry("marker_converter", "MarkerConverter"),
    ConversionPipeline.DOCLING: _ConverterEntry("docling_converter", "DoclingConverter"),
    ConversionPipeline.MINERU: _ConverterEntry("mineru_converter", "MinerUConverter"),
}

_PIPELINE_INFO: dict[ConversionPipeline, ConversionPipelineInfo] = {
    ConversionPipeline.AUTO: ConversionPipelineInfo(
        value="auto",
        label="Auto",
        description="Automatically selects the best pipeline for each file",
        extensions=[
            ".pdf", ".docx", ".xlsx", ".pptx",
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ".bmp", ".tiff", ".tif",
        ],
    ),
    ConversionPipeline.LLM: ConversionPipelineInfo(
        value="llm",
        label="LLM",
        description="Uses vision model for all files",
        extensions=[
            ".pdf", ".docx", ".xlsx", ".pptx",
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ".bmp", ".tiff", ".tif",
        ],
    ),
    ConversionPipeline.MARKER: ConversionPipelineInfo(
        value="marker",
        label="Marker",
        description="Best for PDF documents",
        extensions=[".pdf"],
    ),
    ConversionPipeline.DOCLING: ConversionPipelineInfo(
        value="docling",
        label="Docling",
        description="Best for Office documents",
        extensions=[".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg"],
    ),
    ConversionPipeline.MINERU: ConversionPipelineInfo(
        value="mineru",
        label="MinerU",
        description="High-quality PDF parsing (no XLSX)",
        extensions=[".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg"],
    ),
}

_AUTO_MAPPING: dict[str, ConversionPipeline] = {
    ".pdf": ConversionPipeline.MARKER,
    ".docx": ConversionPipeline.DOCLING,
    ".xlsx": ConversionPipeline.DOCLING,
    ".pptx": ConversionPipeline.DOCLING,
}
_AUTO_DEFAULT = ConversionPipeline.LLM


def resolve_auto_pipeline(filename: str) -> ConversionPipeline:
    """Resolve the AUTO pipeline to a concrete pipeline based on file extension.

    Args:
        filename: The document filename.

    Returns:
        The resolved conversion pipeline.
    """
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return _AUTO_MAPPING.get(suffix, _AUTO_DEFAULT)


def get_converter(pipeline: ConversionPipeline, filename: str = "") -> DocumentConverter:
    """Get a converter instance for the specified pipeline.

    Args:
        pipeline: The conversion pipeline to use.
        filename: The document filename (required when pipeline is AUTO).

    Raises:
        ImportError: If the converter's dependencies are not installed.
        ValueError: If the pipeline is not recognized.
    """
    if pipeline == ConversionPipeline.AUTO:
        pipeline = resolve_auto_pipeline(filename)

    if pipeline not in _CONVERTER_CONFIG:
        raise ValueError(f"Unknown conversion pipeline: {pipeline}")

    entry = _CONVERTER_CONFIG[pipeline]
    module = import_module(f".{entry.module_name}", package=__package__)
    converter_cls = getattr(module, entry.class_name)
    return converter_cls()


def get_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    return list(_PIPELINE_INFO.values())
