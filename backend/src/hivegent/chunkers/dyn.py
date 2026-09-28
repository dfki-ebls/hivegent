from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from hivegent.dyn.segmentation.complex import full_chunking, to_chunks
from hivegent.dyn.serialization.complex.markdown.load import load
from hivegent.dyn.util import get_token_count

from .base import ChunkData, DocumentChunker

__all__ = []


class DynConfig(BaseModel):
    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    math_coref_model: str = Field(
        default="kblw/mathcoref",
        description="Hugging Face model used for math coreference resolution. Used to merge related subsequent chunks.",
    )


@dataclass(slots=True, frozen=True)
class DynChunker(DocumentChunker):
    """Dynamic chunk generation pipeline which focusses on the creation of independent chunks"""

    name = "dyn"
    config: DynConfig = field(default_factory=DynConfig)

    def _split_sync(self, text: str) -> list[ChunkData]:
        doc = load(text)
        full_chunking(doc, self.config.chunk_size, self.config.math_coref_model)
        chunks = to_chunks(doc, self.config.chunk_size)
        return [
            ChunkData(
                text=t.text,
                token_count=get_token_count(t.text),
                start_index=t.start_idx,
                end_index=t.end_idx,
                start_line=t.lines[0],
                end_line=t.lines[1],
            )
            for t in chunks
        ]
