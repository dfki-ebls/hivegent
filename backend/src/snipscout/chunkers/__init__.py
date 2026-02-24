"""Document chunking infrastructure for Hivegent."""

from dataclasses import dataclass
from enum import StrEnum

from .base import DocumentChunker
from .recursive import RecursiveDocumentChunker
from .sentence import SentenceDocumentChunker
from .token import TokenDocumentChunker

__all__ = [
    "ChunkingPipeline",
    "ChunkingPipelineInfo",
    "DocumentChunker",
    "get_chunker",
    "get_chunking_pipelines_info",
    "resolve_auto_pipeline",
]


class ChunkingPipeline(StrEnum):
    """Available chunking pipelines."""

    AUTO = "auto"
    TOKEN = "token"
    SENTENCE = "sentence"
    RECURSIVE = "recursive"


@dataclass(slots=True, frozen=True)
class _ChunkerEntry:
    """Internal registry entry mapping a pipeline to its implementation."""

    chunker_class: type[DocumentChunker]
    label: str
    description: str


@dataclass(slots=True, frozen=True)
class ChunkingPipelineInfo:
    """Public metadata for a chunking pipeline."""

    value: str
    label: str
    description: str


_CHUNKER_CONFIG: dict[ChunkingPipeline, _ChunkerEntry] = {
    ChunkingPipeline.TOKEN: _ChunkerEntry(
        chunker_class=TokenDocumentChunker,
        label="Token",
        description="Fixed token-count chunks for uniform processing",
    ),
    ChunkingPipeline.SENTENCE: _ChunkerEntry(
        chunker_class=SentenceDocumentChunker,
        label="Sentence",
        description="Respects sentence boundaries, good for prose and plain text",
    ),
    ChunkingPipeline.RECURSIVE: _ChunkerEntry(
        chunker_class=RecursiveDocumentChunker,
        label="Recursive",
        description="Hierarchical splitting by headings, paragraphs, and sentences",
    ),
}

_AUTO_MAPPING: dict[str, ChunkingPipeline] = {
    ".md": ChunkingPipeline.RECURSIVE,
    ".html": ChunkingPipeline.RECURSIVE,
    ".xml": ChunkingPipeline.RECURSIVE,
    ".adoc": ChunkingPipeline.RECURSIVE,
    ".txt": ChunkingPipeline.SENTENCE,
    ".csv": ChunkingPipeline.TOKEN,
}

_AUTO_DEFAULT = ChunkingPipeline.RECURSIVE


def resolve_auto_pipeline(filename: str) -> ChunkingPipeline:
    """Resolve the AUTO pipeline to a concrete pipeline based on file extension.

    Args:
        filename: The filename to determine the pipeline for.

    Returns:
        The concrete chunking pipeline to use.
    """
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _AUTO_MAPPING.get(suffix, _AUTO_DEFAULT)


def get_chunker(
    pipeline: ChunkingPipeline,
    filename: str = "",
) -> DocumentChunker:
    """Get a chunker instance for the specified pipeline.

    Args:
        pipeline: The chunking pipeline to use.
        filename: The filename (used for AUTO resolution).

    Returns:
        A configured DocumentChunker instance.

    Raises:
        ValueError: If the pipeline is not recognized.
    """
    if pipeline == ChunkingPipeline.AUTO:
        pipeline = resolve_auto_pipeline(filename)

    if pipeline not in _CHUNKER_CONFIG:
        raise ValueError(f"Unknown chunking pipeline: {pipeline}")

    entry = _CHUNKER_CONFIG[pipeline]
    return entry.chunker_class()


def get_chunking_pipelines_info() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    infos = [
        ChunkingPipelineInfo(
            value=ChunkingPipeline.AUTO.value,
            label="Auto",
            description="Automatically selects the best chunker based on file type",
        ),
    ]
    for entry in _CHUNKER_CONFIG.values():
        infos.append(
            ChunkingPipelineInfo(
                value=entry.chunker_class.name,
                label=entry.label,
                description=entry.description,
            )
        )
    return infos
