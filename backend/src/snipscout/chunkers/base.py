"""Base class for document chunkers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

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

    def __init__(self, chunk_size: int = 2048) -> None:
        """Initialize the chunker.

        Args:
            chunk_size: Target chunk size in tokens.
        """
        self._chunk_size = chunk_size

    @property
    def chunk_size(self) -> int:
        """The target chunk size in tokens."""
        return self._chunk_size

    @property
    @abstractmethod
    def name(self) -> str:
        """The unique name of this chunker."""
        ...

    @abstractmethod
    def chunk(self, text: str) -> list[ChunkData]:
        """Split text into chunks.

        Args:
            text: The document text to chunk.

        Returns:
            List of ChunkData objects.
        """
        ...
