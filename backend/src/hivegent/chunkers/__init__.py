"""Document chunking infrastructure for Hivegent."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_type_hints

from cbrkit.helpers import optional_dependencies
from pydantic import BaseModel

from .base import DocumentChunker
from .fast import FastDocumentChunker
from .late import LateDocumentChunker
from .markdown import MarkdownDocumentChunker
from .neural import NeuralDocumentChunker
from .none import NoneDocumentChunker
from .recursive import RecursiveDocumentChunker
from .sentence import SentenceDocumentChunker
from .slumber import SlumberDocumentChunker
from .table import TableDocumentChunker
from .token import TokenDocumentChunker

__all__ = [
    "ChunkingPipeline",
    "ChunkingPipelineInfo",
    "ChunkingSpec",
    "DocumentChunker",
    "get_chunker",
    "get_chunking_pipelines_info",
]


class ChunkingPipeline(StrEnum):
    """Available chunking pipelines."""

    AUTO = "auto"
    NONE = "none"
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
class ChunkingPipelineInfo:
    """Public metadata for a chunking pipeline."""

    value: str
    label: str
    description: str
    config_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)


_CHUNKERS: dict[ChunkingPipeline, type[DocumentChunker]] = {
    ChunkingPipeline.NONE: NoneDocumentChunker,
    ChunkingPipeline.TOKEN: TokenDocumentChunker,
    ChunkingPipeline.FAST: FastDocumentChunker,
    ChunkingPipeline.SENTENCE: SentenceDocumentChunker,
    ChunkingPipeline.RECURSIVE: RecursiveDocumentChunker,
    ChunkingPipeline.TABLE: TableDocumentChunker,
    ChunkingPipeline.MARKDOWN: MarkdownDocumentChunker,
    ChunkingPipeline.NEURAL: NeuralDocumentChunker,
    ChunkingPipeline.LATE: LateDocumentChunker,
    ChunkingPipeline.SLUMBER: SlumberDocumentChunker,
}

with optional_dependencies():
    from .semantic import SemanticDocumentChunker

    _CHUNKERS[ChunkingPipeline.SEMANTIC] = SemanticDocumentChunker

with optional_dependencies():
    from .code import CodeDocumentChunker

    _CHUNKERS[ChunkingPipeline.CODE] = CodeDocumentChunker


_AUTO_FAST_THRESHOLD = 500_000
"""Content length (in characters) above which AUTO uses Fast instead of Recursive."""


def _config_model(cls: type[DocumentChunker]) -> type[BaseModel] | None:
    """Derive a chunker's Pydantic config model from its ``config`` field."""
    annotation = get_type_hints(cls).get("config")
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def get_chunker(
    pipeline: ChunkingPipeline,
    content_length: int = 0,
    config: dict[str, Any] | None = None,
) -> DocumentChunker:
    """Get a chunker instance for the specified pipeline.

    Args:
        pipeline: The chunking pipeline to use.
        content_length: Length of the document content in characters.
            Only used when *pipeline* is ``AUTO``.
        config: Optional raw config dict to parse into the pipeline's config model.

    Returns:
        A configured DocumentChunker instance.

    Raises:
        ImportError: If the chunker's dependencies are not installed.
        ValidationError: If the config is invalid for the pipeline.
        ValueError: If the pipeline is not recognized.
    """
    if pipeline == ChunkingPipeline.AUTO:
        pipeline = (
            ChunkingPipeline.FAST
            if content_length > _AUTO_FAST_THRESHOLD
            else ChunkingPipeline.RECURSIVE
        )

    cls = _CHUNKERS.get(pipeline)
    if cls is None:
        if pipeline in ChunkingPipeline:
            raise ImportError(
                f"Chunking pipeline '{pipeline.value}' is not available. "
                f"Install its dependencies to enable it."
            )
        raise ValueError(f"Unknown chunking pipeline: {pipeline}")

    kwargs: dict[str, Any] = {}
    if config and (model := _config_model(cls)) is not None:
        kwargs["config"] = model(**config)
    return cls(**kwargs)


def get_chunking_pipelines_info() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    infos = [
        ChunkingPipelineInfo(
            value=ChunkingPipeline.AUTO.value,
            label="Auto",
            description="Automatically selects the best chunker based on file type",
        ),
    ]
    for pipeline, cls in _CHUNKERS.items():
        model = _config_model(cls)
        infos.append(
            ChunkingPipelineInfo(
                value=pipeline.value,
                label=cls.label,
                description=cls.description,
                config_schema=model.model_json_schema() if model else {},
                config_defaults=model().model_dump() if model else {},
            )
        )
    return infos
