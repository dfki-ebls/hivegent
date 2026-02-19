"""Document chunking infrastructure for SnipScout."""

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module

from .base import DocumentChunker

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

    module_name: str
    class_name: str


@dataclass(slots=True, frozen=True)
class ChunkingPipelineInfo:
    """Public metadata for a chunking pipeline."""

    value: str
    label: str
    description: str


_CHUNKER_CONFIG: dict[ChunkingPipeline, _ChunkerEntry] = {
    ChunkingPipeline.TOKEN: _ChunkerEntry("token_chunker", "TokenDocumentChunker"),
    ChunkingPipeline.SENTENCE: _ChunkerEntry(
        "sentence_chunker", "SentenceDocumentChunker"
    ),
    ChunkingPipeline.RECURSIVE: _ChunkerEntry(
        "recursive_chunker", "RecursiveDocumentChunker"
    ),
}

_PIPELINE_INFO: dict[ChunkingPipeline, ChunkingPipelineInfo] = {
    ChunkingPipeline.AUTO: ChunkingPipelineInfo(
        value="auto",
        label="Auto",
        description="Automatically selects the best chunker based on file type",
    ),
    ChunkingPipeline.TOKEN: ChunkingPipelineInfo(
        value="token",
        label="Token",
        description="Fixed token-count chunks for uniform processing",
    ),
    ChunkingPipeline.SENTENCE: ChunkingPipelineInfo(
        value="sentence",
        label="Sentence",
        description="Respects sentence boundaries, good for prose and plain text",
    ),
    ChunkingPipeline.RECURSIVE: ChunkingPipelineInfo(
        value="recursive",
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
    chunk_size: int = 2048,
) -> DocumentChunker:
    """Get a chunker instance for the specified pipeline.

    Args:
        pipeline: The chunking pipeline to use.
        filename: The filename (used for AUTO resolution).
        chunk_size: The target chunk size in tokens.

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
    module = import_module(f".{entry.module_name}", package=__package__)
    chunker_cls = getattr(module, entry.class_name)
    return chunker_cls(chunk_size=chunk_size)


def get_chunking_pipelines_info() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    return list(_PIPELINE_INFO.values())
