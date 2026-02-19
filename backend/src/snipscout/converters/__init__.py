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
    PANDOC = "pandoc"


@dataclass(slots=True, frozen=True)
class _ConverterEntry:
    """Registry entry mapping a pipeline to its implementation and metadata."""

    module_name: str
    class_name: str
    label: str
    description: str
    extensions: frozenset[str]


@dataclass(slots=True, frozen=True)
class ConversionPipelineInfo:
    """Public metadata for a conversion pipeline."""

    value: str
    label: str
    description: str
    extensions: list[str]


_CONVERTER_CONFIG: dict[ConversionPipeline, _ConverterEntry] = {
    ConversionPipeline.LLM: _ConverterEntry(
        module_name="llm_converter",
        class_name="LLMConverter",
        label="LLM",
        description="Uses vision model for all files",
        extensions=frozenset(
            {
                ".pdf",
                ".docx",
                ".xlsx",
                ".pptx",
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".bmp",
                ".tiff",
                ".tif",
            }
        ),
    ),
    ConversionPipeline.MARKER: _ConverterEntry(
        module_name="marker_converter",
        class_name="MarkerConverter",
        label="Marker",
        description="Best for PDF documents",
        extensions=frozenset({".pdf"}),
    ),
    ConversionPipeline.DOCLING: _ConverterEntry(
        module_name="docling_converter",
        class_name="DoclingConverter",
        label="Docling",
        description="Best for Office documents",
        extensions=frozenset(
            {
                ".pdf",
                ".docx",
                ".pptx",
                ".xlsx",
                ".png",
                ".jpg",
                ".jpeg",
            }
        ),
    ),
    ConversionPipeline.MINERU: _ConverterEntry(
        module_name="mineru_converter",
        class_name="MinerUConverter",
        label="MinerU",
        description="High-quality PDF parsing (no XLSX)",
        extensions=frozenset(
            {
                ".pdf",
                ".docx",
                ".pptx",
                ".png",
                ".jpg",
                ".jpeg",
            }
        ),
    ),
    ConversionPipeline.PANDOC: _ConverterEntry(
        module_name="pandoc_converter",
        class_name="PandocConverter",
        label="Pandoc",
        description="Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
        "DocBook, Typst, and more",
        extensions=frozenset(
            {
                ".txt",
                ".html",
                ".xml",
                ".csv",
                ".adoc",
                ".odt",
                ".rst",
                ".rtf",
                ".epub",
                ".tex",
                ".org",
                ".docbook",
                ".typst",
                ".docx",
                ".pptx",
                ".xlsx",
                ".fb2",
                ".opml",
                ".bib",
                ".ris",
                ".tsv",
                ".ipynb",
                ".textile",
                ".creole",
                ".djot",
                ".dokuwiki",
                ".mediawiki",
                ".tikiwiki",
                ".twiki",
                ".vimwiki",
                ".jira",
                ".muse",
                ".t2t",
                ".jats",
                ".man",
                ".pod",
            }
        ),
    ),
}

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
    and lazily imports the converter module.

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
        raise ValueError(f"Unknown conversion pipeline: {pipeline}")

    entry = _CONVERTER_CONFIG[pipeline]

    if filename:
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if suffix and suffix not in entry.extensions:
            raise ValueError(
                f"Conversion pipeline '{pipeline.value}' does not support "
                f"{suffix}. Supported: {', '.join(sorted(entry.extensions))}"
            )

    module = import_module(f".{entry.module_name}", package=__package__)
    converter_cls = getattr(module, entry.class_name)
    return converter_cls()


def get_pipelines_info() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    all_extensions = sorted(
        {ext for e in _CONVERTER_CONFIG.values() for ext in e.extensions}
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
        infos.append(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=entry.label,
                description=entry.description,
                extensions=sorted(entry.extensions),
            )
        )
    return infos
