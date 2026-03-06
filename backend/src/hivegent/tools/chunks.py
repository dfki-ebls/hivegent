"""Chunk tool callables — list and get chunk data."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, override

from pydantic import Field

from .documents import DocumentFilenameArg
from .typing import Tool

__all__ = ["ChunkIndexArg", "GetChunkTool", "ListChunksTool"]

ChunkIndexArg = Annotated[
    int,
    Field(description="Zero-based index of the chunk to retrieve.", ge=0),
]


@dataclass(slots=True, frozen=True)
class ListChunksTool[T](Tool):
    """List chunk metadata for a document."""

    loader: Callable[[str], Sequence[T] | None]

    @override
    def __call__(self, filename: DocumentFilenameArg) -> list[T] | None:
        """List chunk metadata for a document."""
        result = self.loader(filename)
        return list(result) if result is not None else None


@dataclass(slots=True, frozen=True)
class GetChunkTool[T](Tool):
    """Get the content of a specific chunk."""

    loader: Callable[[str, int], T | None]

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        chunk_index: ChunkIndexArg,
    ) -> T | None:
        """Get the content of a specific chunk."""
        return self.loader(filename, chunk_index)
