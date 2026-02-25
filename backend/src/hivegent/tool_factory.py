"""Project-specific tool factory for constructing configured tool instances."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from .chunks import load_chunked_document, rechunk_document
from .config import DOCUMENT_EXTENSION, settings
from .retrieval import search_multi
from .store import Casebase
from .tools import (
    EditDocumentTool,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepTool,
    ListChunksTool,
    ListDocumentsTool,
    SearchTool,
    WriteDocumentTool,
)
from .types import ChunkSummary, DocumentFilter

__all__ = ["ToolFactory"]


@dataclass(slots=True, frozen=True)
class ToolFactory:
    """Create configured tool instances for a specific store context."""

    store: Casebase
    document_filter: DocumentFilter | None = None
    group_stores: tuple[Casebase, ...] = ()
    group_filters: dict[str, DocumentFilter] = field(default_factory=dict)

    @property
    def list_documents(self) -> ListDocumentsTool:
        """Create a ListDocumentsTool for the user's document directory."""
        return ListDocumentsTool(
            path=self.store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
        )

    @property
    def get_document(self) -> GetDocumentTool:
        """Create a GetDocumentTool for the user's document directory."""
        return GetDocumentTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    @property
    def get_document_lines(self) -> GetDocumentLinesTool:
        """Create a GetDocumentLinesTool for the user's document directory."""
        return GetDocumentLinesTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    @property
    def glob_documents(self) -> GlobDocumentsTool:
        """Create a GlobDocumentsTool for the user's document directory."""
        return GlobDocumentsTool(
            path=self.store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
        )

    @property
    def grep(self) -> GrepTool:
        """Create a GrepTool for the user's document directory."""
        return GrepTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    def _make_search(self, search_type: Literal["dense", "sparse"]) -> SearchTool:
        """Create a SearchTool for the given search type.

        Args:
            search_type: ``"dense"`` for vector embeddings, ``"sparse"`` for BM25/FTS.
        """
        return SearchTool(
            search_fn=partial(
                search_multi,
                (self.store, *self.group_stores),
                search_type,
                document_filter=self.document_filter,
                group_filters=self.group_filters,
            ),
        )

    @property
    def dense_search(self) -> SearchTool:
        """Create a SearchTool using dense vector embeddings."""
        return self._make_search("dense")

    @property
    def sparse_search(self) -> SearchTool:
        """Create a SearchTool using sparse BM25/FTS matching."""
        return self._make_search("sparse")

    @property
    def list_chunks(self) -> ListChunksTool:
        """Create a ListChunksTool for the user's chunks directory."""
        chunks_dir = self.store.chunks_dir(settings.data_dir)

        def _loader(filename: str) -> Sequence[ChunkSummary] | None:
            chunked = load_chunked_document(chunks_dir, filename)
            if not chunked:
                return None
            return [
                ChunkSummary(
                    token_count=c.token_count,
                    start_index=c.start_index,
                    end_index=c.end_index,
                )
                for c in chunked.chunks
            ]

        return ListChunksTool(
            loader=_loader,
            document_filter=self.document_filter,
        )

    @property
    def get_chunk(self) -> GetChunkTool:
        """Create a GetChunkTool for the user's chunks directory."""
        chunks_dir = self.store.chunks_dir(settings.data_dir)

        def _loader(filename: str, chunk_index: int) -> str | None:
            chunked = load_chunked_document(chunks_dir, filename)
            if not chunked:
                return None
            if 0 <= chunk_index < len(chunked.chunks):
                return chunked.chunks[chunk_index].text
            return None

        return GetChunkTool(
            loader=_loader,
            document_filter=self.document_filter,
        )

    @property
    def edit_document(self) -> EditDocumentTool:
        """Create an EditDocumentTool with automatic re-chunking."""
        store = self.store

        def _on_write(filename: str) -> None:
            rechunk_document(store, filename)

        return EditDocumentTool(
            path=store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
            on_write=_on_write,
        )

    @property
    def write_document(self) -> WriteDocumentTool:
        """Create a WriteDocumentTool with automatic re-chunking."""
        store = self.store

        def _on_write(filename: str) -> None:
            rechunk_document(store, filename)

        return WriteDocumentTool(
            path=store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
            on_write=_on_write,
        )
