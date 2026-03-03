"""Document chunking infrastructure for Hivegent."""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cbrkit.helpers import optional_dependencies
from pydantic import BaseModel, ValidationError

from .base import DocumentChunker
from .fast import FastChunkerConfig, FastDocumentChunker
from .late import LateChunkerConfig, LateDocumentChunker
from .markdown import MarkdownChunkerConfig, MarkdownDocumentChunker
from .neural import NeuralChunkerConfig, NeuralDocumentChunker
from .recursive import RecursiveChunkerConfig, RecursiveDocumentChunker
from .sentence import SentenceChunkerConfig, SentenceDocumentChunker
from .slumber import SlumberChunkerConfig, SlumberDocumentChunker
from .table import TableChunkerConfig, TableDocumentChunker
from .token import TokenChunkerConfig, TokenDocumentChunker

__all__ = [
    "ChunkingPipeline",
    "ChunkingPipelineInfo",
    "ChunkingSpec",
    "DocumentChunker",
    "get_chunker",
    "get_chunking_pipelines_info",
    "validate_chunking_config",
]

logger = logging.getLogger(__name__)


class ChunkingPipeline(StrEnum):
    """Available chunking pipelines."""

    AUTO = "auto"
    TOKEN = "token"
    FAST = "fast"
    SENTENCE = "sentence"
    RECURSIVE = "recursive"
    TABLE = "table"
    MARKDOWN = "markdown"
    SEMANTIC = "semantic"
    CODE = "code"
    NEURAL = "neural"
    LATE = "late"
    SLUMBER = "slumber"


class ChunkingSpec(BaseModel):
    """Chunking pipeline selection and configuration."""

    pipeline: ChunkingPipeline = ChunkingPipeline.AUTO
    config: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class _ChunkerEntry:
    """Internal registry entry mapping a pipeline to its implementation."""

    chunker_class: type[DocumentChunker]
    label: str
    description: str
    config_model: type[BaseModel] | None = None


@dataclass(slots=True, frozen=True)
class ChunkingPipelineInfo:
    """Public metadata for a chunking pipeline."""

    value: str
    label: str
    description: str
    config_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)


# Core chunkers (always available)
_CHUNKER_CONFIG: dict[ChunkingPipeline, _ChunkerEntry] = {
    ChunkingPipeline.TOKEN: _ChunkerEntry(
        chunker_class=TokenDocumentChunker,
        label="Token",
        description="Fixed token-count chunks for uniform processing",
        config_model=TokenChunkerConfig,
    ),
    ChunkingPipeline.FAST: _ChunkerEntry(
        chunker_class=FastDocumentChunker,
        label="Fast",
        description="High-throughput delimiter-based splitting",
        config_model=FastChunkerConfig,
    ),
    ChunkingPipeline.SENTENCE: _ChunkerEntry(
        chunker_class=SentenceDocumentChunker,
        label="Sentence",
        description="Respects sentence boundaries, good for prose and plain text",
        config_model=SentenceChunkerConfig,
    ),
    ChunkingPipeline.RECURSIVE: _ChunkerEntry(
        chunker_class=RecursiveDocumentChunker,
        label="Recursive",
        description="Hierarchical splitting by headings, paragraphs, and sentences",
        config_model=RecursiveChunkerConfig,
    ),
    ChunkingPipeline.TABLE: _ChunkerEntry(
        chunker_class=TableDocumentChunker,
        label="Table",
        description="Row-based splitting for tabular data",
        config_model=TableChunkerConfig,
    ),
    ChunkingPipeline.MARKDOWN: _ChunkerEntry(
        chunker_class=MarkdownDocumentChunker,
        label="Markdown",
        description="Parses markdown into semantic elements (text, tables, code)",
        config_model=MarkdownChunkerConfig,
    ),
    ChunkingPipeline.NEURAL: _ChunkerEntry(
        chunker_class=NeuralDocumentChunker,
        label="Neural",
        description="Neural model-based chunk boundary detection",
        config_model=NeuralChunkerConfig,
    ),
    ChunkingPipeline.LATE: _ChunkerEntry(
        chunker_class=LateDocumentChunker,
        label="Late",
        description="Late-interaction embedding-aware chunk boundaries",
        config_model=LateChunkerConfig,
    ),
    ChunkingPipeline.SLUMBER: _ChunkerEntry(
        chunker_class=SlumberDocumentChunker,
        label="Slumber",
        description="LLM-guided intelligent chunk boundary decisions",
        config_model=SlumberChunkerConfig,
    ),
}

# Optional chunkers (registered only when their dependencies are installed)
with optional_dependencies():
    from .semantic import SemanticChunkerConfig, SemanticDocumentChunker

    _CHUNKER_CONFIG[ChunkingPipeline.SEMANTIC] = _ChunkerEntry(
        chunker_class=SemanticDocumentChunker,
        label="Semantic",
        description="Splits by semantic similarity using embeddings",
        config_model=SemanticChunkerConfig,
    )

with optional_dependencies():
    from .code import CodeChunkerConfig, CodeDocumentChunker

    _CHUNKER_CONFIG[ChunkingPipeline.CODE] = _ChunkerEntry(
        chunker_class=CodeDocumentChunker,
        label="Code",
        description="Syntax-aware splitting using tree-sitter",
        config_model=CodeChunkerConfig,
    )

_AUTO_FAST_THRESHOLD = 500_000
"""Content length (in characters) above which AUTO uses Fast instead of Recursive."""


def _resolve_auto(content_length: int) -> ChunkingPipeline:
    if content_length > _AUTO_FAST_THRESHOLD:
        return ChunkingPipeline.FAST
    return ChunkingPipeline.RECURSIVE


def get_chunker(
    pipeline: ChunkingPipeline,
    content_length: int = 0,
) -> DocumentChunker:
    """Get a chunker instance for the specified pipeline.

    Args:
        pipeline: The chunking pipeline to use.
        content_length: Length of the document content in characters.
            Only used when *pipeline* is ``AUTO``.

    Returns:
        A configured DocumentChunker instance.

    Raises:
        ImportError: If the chunker's dependencies are not installed.
        ValueError: If the pipeline is not recognized.
    """
    if pipeline == ChunkingPipeline.AUTO:
        pipeline = _resolve_auto(content_length)

    if pipeline not in _CHUNKER_CONFIG:
        if pipeline in ChunkingPipeline:
            raise ImportError(
                f"Chunking pipeline '{pipeline.value}' is not available. "
                f"Install its dependencies to enable it."
            )
        raise ValueError(f"Unknown chunking pipeline: {pipeline}")

    entry = _CHUNKER_CONFIG[pipeline]
    return entry.chunker_class()


def validate_chunking_config(spec: ChunkingSpec) -> dict[str, Any] | None:
    """Validate a chunking config dict against the pipeline's config model.

    For ``AUTO`` pipelines, validation is skipped since the concrete pipeline
    is not known until file extension resolution.

    Args:
        spec: The chunking spec containing pipeline and config.

    Returns:
        The validated and normalized config dict, or ``None`` if no config.

    Raises:
        ValidationError: If the config is invalid for the pipeline.
    """
    if spec.config is None or spec.pipeline == ChunkingPipeline.AUTO:
        return spec.config
    entry = _CHUNKER_CONFIG.get(spec.pipeline)
    if entry is None or entry.config_model is None:
        return spec.config
    validated = entry.config_model(**spec.config)
    return validated.model_dump()


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
            ChunkingPipelineInfo(
                value=entry.chunker_class.name,
                label=entry.label,
                description=entry.description,
                config_schema=config_schema,
                config_defaults=config_defaults,
            )
        )
    return infos
