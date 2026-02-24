"""Document conversion infrastructure for Hivegent."""

from dataclasses import dataclass
from enum import StrEnum

from cbrkit.helpers import optional_dependencies

from .base import DocumentConverter
from .llm import LLMConverter
from .pandoc import PandocConverter

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
    MARKITDOWN = "markitdown"
    KREUZBERG = "kreuzberg"


@dataclass(slots=True, frozen=True)
class _ConverterEntry:
    """Registry entry mapping a pipeline to its converter class and metadata."""

    converter_class: type[DocumentConverter]
    label: str
    description: str


@dataclass(slots=True, frozen=True)
class ConversionPipelineInfo:
    """Public metadata for a conversion pipeline."""

    value: str
    label: str
    description: str
    extensions: list[str]


# Core converters (always available)
_CONVERTER_CONFIG: dict[ConversionPipeline, _ConverterEntry] = {
    ConversionPipeline.LLM: _ConverterEntry(
        converter_class=LLMConverter,
        label="LLM",
        description="Uses vision model for all files",
    ),
    ConversionPipeline.PANDOC: _ConverterEntry(
        converter_class=PandocConverter,
        label="Pandoc",
        description="Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, "
        "DocBook, Typst, and more",
    ),
}

# Optional converters (registered only when their dependencies are installed)
with optional_dependencies():
    from .marker import MarkerConverter

    _CONVERTER_CONFIG[ConversionPipeline.MARKER] = _ConverterEntry(
        converter_class=MarkerConverter,
        label="Marker",
        description="Best for PDF documents",
    )

with optional_dependencies():
    from .docling import DoclingConverter

    _CONVERTER_CONFIG[ConversionPipeline.DOCLING] = _ConverterEntry(
        converter_class=DoclingConverter,
        label="Docling",
        description="Best for Office documents",
    )

with optional_dependencies():
    from .mineru import MinerUConverter

    _CONVERTER_CONFIG[ConversionPipeline.MINERU] = _ConverterEntry(
        converter_class=MinerUConverter,
        label="MinerU",
        description="High-quality PDF parsing (no XLSX)",
    )

with optional_dependencies():
    from .markitdown import MarkItDownConverter

    _CONVERTER_CONFIG[ConversionPipeline.MARKITDOWN] = _ConverterEntry(
        converter_class=MarkItDownConverter,
        label="MarkItDown",
        description="Microsoft's converter for Office, PDF, images, and more",
    )

with optional_dependencies():
    from .kreuzberg import KreuzbergConverter

    _CONVERTER_CONFIG[ConversionPipeline.KREUZBERG] = _ConverterEntry(
        converter_class=KreuzbergConverter,
        label="Kreuzberg",
        description="Text extraction from 75+ formats with OCR support",
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
        infos.append(
            ConversionPipelineInfo(
                value=pipeline.value,
                label=entry.label,
                description=entry.description,
                extensions=sorted(entry.converter_class.extensions),
            )
        )
    return infos
