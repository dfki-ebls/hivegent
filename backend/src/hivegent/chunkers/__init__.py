"""Document chunking infrastructure for Hivegent."""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Any

from pydantic import BaseModel

from ..l10n import Localized
from ..pipeline_registry import (
    PipelineConfigInfo,
    PipelineImplementation,
    PipelineRegistration,
)
from .base import DocumentChunker

__all__ = [
    "ChunkingPipeline",
    "ChunkingPipelineInfo",
    "ChunkingSpec",
    "DocumentChunker",
    "get_chunker",
    "get_chunking_pipeline_config",
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


def _load_none() -> PipelineImplementation[DocumentChunker]:
    from .none import NoneDocumentChunker

    return PipelineImplementation(NoneDocumentChunker)


def _load_token() -> PipelineImplementation[DocumentChunker]:
    from .token import TokenChunkerConfig, TokenDocumentChunker

    return PipelineImplementation(TokenDocumentChunker, TokenChunkerConfig)


def _load_fast() -> PipelineImplementation[DocumentChunker]:
    from .fast import FastChunkerConfig, FastDocumentChunker

    return PipelineImplementation(FastDocumentChunker, FastChunkerConfig)


def _load_sentence() -> PipelineImplementation[DocumentChunker]:
    from .sentence import SentenceChunkerConfig, SentenceDocumentChunker

    return PipelineImplementation(SentenceDocumentChunker, SentenceChunkerConfig)


def _load_recursive() -> PipelineImplementation[DocumentChunker]:
    from .recursive import RecursiveChunkerConfig, RecursiveDocumentChunker

    return PipelineImplementation(RecursiveDocumentChunker, RecursiveChunkerConfig)


def _load_table() -> PipelineImplementation[DocumentChunker]:
    from .table import TableChunkerConfig, TableDocumentChunker

    return PipelineImplementation(TableDocumentChunker, TableChunkerConfig)


def _load_markdown() -> PipelineImplementation[DocumentChunker]:
    from .markdown import MarkdownChunkerConfig, MarkdownDocumentChunker

    return PipelineImplementation(MarkdownDocumentChunker, MarkdownChunkerConfig)


def _load_semantic() -> PipelineImplementation[DocumentChunker]:
    from .semantic import SemanticChunkerConfig, SemanticDocumentChunker

    return PipelineImplementation(SemanticDocumentChunker, SemanticChunkerConfig)


def _load_code() -> PipelineImplementation[DocumentChunker]:
    from .code import CodeChunkerConfig, CodeDocumentChunker

    return PipelineImplementation(CodeDocumentChunker, CodeChunkerConfig)


def _load_neural() -> PipelineImplementation[DocumentChunker]:
    from .neural import NeuralChunkerConfig, NeuralDocumentChunker

    return PipelineImplementation(NeuralDocumentChunker, NeuralChunkerConfig)


def _load_late() -> PipelineImplementation[DocumentChunker]:
    from .late import LateChunkerConfig, LateDocumentChunker

    return PipelineImplementation(LateDocumentChunker, LateChunkerConfig)


def _load_slumber() -> PipelineImplementation[DocumentChunker]:
    from .slumber import SlumberChunkerConfig, SlumberDocumentChunker

    return PipelineImplementation(SlumberDocumentChunker, SlumberChunkerConfig)


_CHUNKERS: dict[ChunkingPipeline, PipelineRegistration[DocumentChunker]] = {
    ChunkingPipeline.NONE: PipelineRegistration(
        loader=_load_none,
        label=Localized(en="None", de="Keine"),
        description=Localized(
            en="Keep the full document as a single chunk",
            de="Behält das gesamte Dokument als einen einzigen Chunk",
        ),
    ),
    ChunkingPipeline.TOKEN: PipelineRegistration(
        loader=_load_token,
        label=Localized(en="Token", de="Token"),
        description=Localized(
            en="Fixed token-count chunks for uniform processing",
            de="Chunks mit fester Tokenanzahl für eine gleichmäßige Verarbeitung",
        ),
    ),
    ChunkingPipeline.FAST: PipelineRegistration(
        loader=_load_fast,
        label=Localized(en="Fast", de="Schnell"),
        description=Localized(
            en="High-throughput delimiter-based splitting",
            de="Schnelle Aufteilung anhand von Trennzeichen",
        ),
    ),
    ChunkingPipeline.SENTENCE: PipelineRegistration(
        loader=_load_sentence,
        label=Localized(en="Sentence", de="Satz"),
        description=Localized(
            en="Respects sentence boundaries, good for prose and plain text",
            de="Berücksichtigt Satzgrenzen, gut für Fließtext und reinen Text",
        ),
    ),
    ChunkingPipeline.RECURSIVE: PipelineRegistration(
        loader=_load_recursive,
        label=Localized(en="Recursive", de="Rekursiv"),
        description=Localized(
            en="Hierarchical splitting by headings, paragraphs, and sentences",
            de="Hierarchische Aufteilung nach Überschriften, Absätzen und Sätzen",
        ),
    ),
    ChunkingPipeline.TABLE: PipelineRegistration(
        loader=_load_table,
        label=Localized(en="Table", de="Tabelle"),
        description=Localized(
            en="Row-based splitting for tabular data",
            de="Zeilenweise Aufteilung für tabellarische Daten",
        ),
    ),
    ChunkingPipeline.MARKDOWN: PipelineRegistration(
        loader=_load_markdown,
        label=Localized(en="Markdown", de="Markdown"),
        description=Localized(
            en="Parses markdown into semantic elements (text, tables, code)",
            de="Zerlegt Markdown in semantische Elemente (Text, Tabellen, Code)",
        ),
    ),
    ChunkingPipeline.SEMANTIC: PipelineRegistration(
        loader=_load_semantic,
        label=Localized(en="Semantic", de="Semantisch"),
        description=Localized(
            en="Splits by semantic similarity using embeddings",
            de="Teilt nach semantischer Ähnlichkeit mithilfe von Embeddings",
        ),
        dependencies=("model2vec",),
    ),
    ChunkingPipeline.CODE: PipelineRegistration(
        loader=_load_code,
        label=Localized(en="Code", de="Code"),
        description=Localized(
            en="Syntax-aware splitting using tree-sitter",
            de="Syntaxbewusste Aufteilung mit tree-sitter",
        ),
        dependencies=("tree_sitter_language_pack",),
    ),
    ChunkingPipeline.NEURAL: PipelineRegistration(
        loader=_load_neural,
        label=Localized(en="Neural", de="Neuronal"),
        description=Localized(
            en="Neural model-based chunk boundary detection",
            de="Erkennt Chunkgrenzen mit einem neuronalen Modell",
        ),
    ),
    ChunkingPipeline.LATE: PipelineRegistration(
        loader=_load_late,
        label=Localized(en="Late", de="Late"),
        description=Localized(
            en="Late-interaction embedding-aware chunk boundaries",
            de="Chunkgrenzen auf Basis von Late-Interaction-Embeddings",
        ),
    ),
    ChunkingPipeline.SLUMBER: PipelineRegistration(
        loader=_load_slumber,
        label=Localized(en="Slumber", de="Slumber"),
        description=Localized(
            en="LLM-guided intelligent chunk boundary decisions",
            de="Intelligente Chunkgrenzen, vom LLM bestimmt",
        ),
    ),
}


def _not_available(name: str) -> Localized[str]:
    return Localized(
        en=f"Chunking pipeline '{name}' is not available",
        de=f"Die Chunking-Pipeline „{name}“ ist nicht verfügbar",
    )


_AUTO_LABEL = Localized(en="Auto", de="Automatisch")
_AUTO_DESCRIPTION = Localized(
    en="Recommended default: structure-aware recursive splitting",
    de="Empfohlener Standard: strukturbewusste rekursive Aufteilung",
)


def get_chunker(
    pipeline: ChunkingPipeline,
    config: dict[str, Any] | None = None,
) -> DocumentChunker:
    """Get a chunker instance for the specified pipeline.

    ``AUTO`` resolves to :attr:`ChunkingPipeline.RECURSIVE`: it is
    dependency-free, markdown/prose-aware, size-bounded, and fast enough
    to chunk multi-megabyte documents in well under a second, so there is
    no document size at which a lower-quality fallback pays off.

    Args:
        pipeline: The chunking pipeline to use.
        config: Optional raw config dict to parse into the pipeline's config model.

    Returns:
        A configured DocumentChunker instance.

    Raises:
        ImportError: If the chunker's dependencies are not installed.
        ValidationError: If the config is invalid for the pipeline.
    """
    if pipeline == ChunkingPipeline.AUTO:
        pipeline = ChunkingPipeline.RECURSIVE

    try:
        implementation = _CHUNKERS[pipeline].load(pipeline.value)
    except ImportError as exc:
        raise ImportError(
            f"Chunking pipeline '{pipeline.value}' is not available. "
            "Install its dependencies to enable it."
        ) from exc

    kwargs: dict[str, Any] = {}
    if config and implementation.config is not None:
        kwargs["config"] = implementation.config(**config)

    return implementation.cls(**kwargs)


def get_chunking_pipelines_info() -> list[ChunkingPipelineInfo]:
    """Get dependency-free metadata for installed chunking pipelines.

    Labels and descriptions are in the language of the request being served.
    """
    return [
        ChunkingPipelineInfo(
            value=ChunkingPipeline.AUTO.value,
            label=_AUTO_LABEL.current,
            description=_AUTO_DESCRIPTION.current,
        ),
        *(
            ChunkingPipelineInfo(
                value=pipeline.value,
                label=registration.label.current,
                description=registration.description.current,
            )
            for pipeline, registration in _CHUNKERS.items()
            if registration.available
        ),
    ]


@cache
def get_chunking_pipeline_config(
    pipeline: ChunkingPipeline,
) -> PipelineConfigInfo:
    """Get configuration metadata for one selected chunking pipeline."""
    registration = _CHUNKERS.get(pipeline)
    if registration is None or not registration.available:
        raise ValueError(_not_available(pipeline.value).current)

    return registration.config_info(pipeline.value)
