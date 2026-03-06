"""Chunk tool callables — list and get chunk data."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from typing import override

from .typing import Tool

__all__ = ["GetChunkTool", "ListChunksTool"]


@dataclass(slots=True, frozen=True)
class ListChunksTool[T](Tool):
    """List chunk metadata for a document."""

    loader: Callable[[str], Sequence[T] | None]

    @override
    def __call__(self, filename: str) -> list[T] | None:
        """List chunk metadata for a document.

        Args:
            filename: The document filename.
        """
        result = self.loader(filename)
        return list(result) if result is not None else None


@dataclass(slots=True, frozen=True)
class GetChunkTool[T](Tool):
    """Get the content of a specific chunk."""

    loader: Callable[[str, int], T | None]

    @override
    def __call__(
        self,
        filename: str,
        chunk_index: int,
    ) -> T | None:
        """Get the content of a specific chunk.

        Args:
            filename: The document filename.
            chunk_index: The index of the chunk to retrieve.
        """
        return self.loader(filename, chunk_index)
