"""Document chunking infrastructure for Hivegent."""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import DocumentChunker
from .recursive import RecursiveChunkerConfig, RecursiveDocumentChunker
from .sentence import SentenceChunkerConfig, SentenceDocumentChunker
from .token import TokenChunkerConfig, TokenDocumentChunker

__all__ = [
    "ChunkingPipeline",
    "ChunkingPipelineInfo",
    "ChunkingSpec",
    "DocumentChunker",
    "get_chunker",
    "get_chunking_pipelines_info",
    "resolve_auto_pipeline",
    "validate_chunking_config",
]

logger = logging.getLogger(__name__)


class ChunkingPipeline(StrEnum):
    """Available chunking pipelines."""

    AUTO = "auto"
    TOKEN = "token"
    SENTENCE = "sentence"
    RECURSIVE = "recursive"


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


_CHUNKER_CONFIG: dict[ChunkingPipeline, _ChunkerEntry] = {
    ChunkingPipeline.TOKEN: _ChunkerEntry(
        chunker_class=TokenDocumentChunker,
        label="Token",
        description="Fixed token-count chunks for uniform processing",
        config_model=TokenChunkerConfig,
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
