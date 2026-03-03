"""Base class for document chunkers."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal

from chonkie import OverlapRefinery
from chonkie.types.base import Chunk
from pydantic import BaseModel, Field

__all__ = [
    "BaseChonkieConfig",
    "ChunkData",
    "DocumentChunker",
    "ChonkieOverlapConfig",
    "ChonkieRefineryConfig",
    "apply_chonkie",
]


@dataclass(slots=True, frozen=True)
class ChunkData:
    """A single chunk produced by a document chunker."""

    text: str
    token_count: int
    start_index: int
    end_index: int
    index: int


class DocumentChunker(ABC):
    """Abstract base class for document chunkers.

    All chunkers must inherit from this class and implement the required
    properties and methods.
    Chunkers split document text into smaller pieces for retrieval.
    """

    name: ClassVar[str]

    @abstractmethod
    def __call__(
        self,
        text: str,
        /,
        config: dict[str, Any] | None = None,
    ) -> list[ChunkData]:
        """Split text into chunks.

        Args:
            text: The document text to chunk.
            config: Optional pipeline-specific configuration dict.

        Returns:
            List of ChunkData objects.
        """
        ...


class ChonkieOverlapConfig(BaseModel):
    """Config for the chonkie OverlapRefinery."""

    type: Literal["overlap"] = "overlap"
    context_size: float = Field(default=0.25, ge=0)
    mode: Literal["token", "recursive"] = "token"
    method: Literal["suffix", "prefix"] = "suffix"
    merge: bool = True


# Tagged union — extend with `| NewRefineryConfig` when adding more refineries
ChonkieRefineryConfig = Annotated[ChonkieOverlapConfig, Field(discriminator="type")]


class BaseChonkieConfig(BaseModel):
    """Shared config for all chonkie-based chunkers."""

    refineries: list[ChonkieRefineryConfig] = Field(default_factory=list)


def apply_chonkie(
    chunks: Sequence[Chunk],
    refineries: Sequence[ChonkieRefineryConfig],
) -> list[ChunkData]:
    """Convert chonkie Chunk objects to ChunkData and apply refineries.

    Args:
        chunks: Sequence of chonkie Chunk objects.
        refineries: Sequence of refinery configs to apply sequentially.

    Returns:
        List of refined ChunkData with sequential index values.
    """

    raw: Sequence[Chunk] = chunks
    for cfg in refineries:
        if isinstance(cfg, ChonkieOverlapConfig):
            raw = OverlapRefinery(
                context_size=cfg.context_size,
                mode=cfg.mode,
                method=cfg.method,
                merge=cfg.merge,
            ).refine(list(raw))
    return [
        ChunkData(
            text=c.text,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
            index=i,
        )
        for i, c in enumerate(raw)
    ]
