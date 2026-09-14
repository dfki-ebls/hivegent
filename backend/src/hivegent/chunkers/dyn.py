from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from dyn.segmentation.complex import full_chunking, to_chunks
from dyn.serialization.complex.markdown.load import load
from dyn.util import get_token_count

from .base import ChunkData, DocumentChunker

__all__ = []


class DynConfig(BaseModel):
    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )


@dataclass(slots=True, frozen=True)
class DynChunker(DocumentChunker):
    """ """

    name = "dyn"
    config: DynConfig = field(default_factory=DynConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        doc = load(text)
        full_chunking(doc, self.config.chunk_size)
        chunks = to_chunks(doc, self.config.chunk_size)
        return [
            ChunkData(
                text=t.text,
                token_count=get_token_count(t.text),
                start_index=t.start_idx,
                end_index=t.end_idx,
            )
            for t in chunks
        ]
