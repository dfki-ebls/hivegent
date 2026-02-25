"""Base class for document chunkers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = ["ChunkData", "DocumentChunker"]


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
    chunk_size: ClassVar[int]

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
