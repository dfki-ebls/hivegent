"""Code-aware document chunker using chonkie."""

from dataclasses import dataclass, field
from functools import lru_cache

from chonkie import CodeChunker
from pydantic import Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["CodeChunkerConfig", "CodeDocumentChunker"]


class CodeChunkerConfig(BaseChonkieConfig):
    """Configuration for the Code chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    language: str = Field(
        default="auto",
        description="Programming language for parsing (e.g. 'python', 'auto').",
    )
    include_nodes: bool = Field(
        default=False,
        description="Whether to include tree-sitter node information in chunks.",
    )


@lru_cache(maxsize=4)
def _build_chunker(config_json: str) -> CodeChunker:
    config = CodeChunkerConfig.model_validate_json(config_json)
    return CodeChunker(
        chunk_size=config.chunk_size,
        language=config.language,
        include_nodes=config.include_nodes,
    )


@dataclass(slots=True, frozen=True)
class CodeDocumentChunker(DocumentChunker):
    """Chunker that splits source code respecting syntax boundaries.

    Uses chonkie's CodeChunker with tree-sitter for AST-aware splitting.
    Best suited for source code files.
    Requires ``chonkie[code]`` (tree-sitter-language-pack).
    """

    name = "code"
    config: CodeChunkerConfig = field(default_factory=CodeChunkerConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        chunks = _build_chunker(self.config.model_dump_json()).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)
