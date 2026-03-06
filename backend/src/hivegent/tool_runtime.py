"""Shared runtime helpers for document-oriented agent and MCP tools."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from .chunks import load_document_metadata, rechunk_document
from .config import settings
from .retrieval import apply_search_tool, mark_dirty
from .store import Casebase
from .tools import (
    DocumentRange,
    DocumentSummary,
    EditDocumentTool,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    ListChunksTool,
    ListDocumentsTool,
    WriteDocumentTool,
)
from .types import ChunkSummary, DocumentFilter, RetrievedChunk

__all__ = [
    "edit_document_text",
    "get_document_chunk",
    "get_document_lines",
    "get_document_text",
    "glob_documents",
    "grep_documents",
    "list_document_chunks",
    "list_document_summaries",
    "semantic_search_documents",
    "write_document_text",
]


def _workspace_dir(store: Casebase) -> Path:
    return store.workspace_dir(settings.data_dir)


def _metadata_dir(store: Casebase) -> Path:
    return store.metadata_dir(settings.data_dir)


def list_document_summaries(
    store: Casebase,
    *,
    subdir: str | None = None,
    max_depth: int | None = None,
    document_filter: DocumentFilter | None = None,
) -> list[DocumentSummary]:
    """List documents in a store with optional filtering."""
    tool = ListDocumentsTool(path=_workspace_dir(store), extension="")
    results = tool(subdir=subdir, max_depth=max_depth)
    if document_filter:
        results = [result for result in results if document_filter(result.filename)]
    return results


def glob_documents(
    store: Casebase,
    pattern: str,
    *,
    document_filter: DocumentFilter | None = None,
) -> list[str]:
    """Expand a glob pattern within a store."""
    tool = GlobDocumentsTool(path=_workspace_dir(store), extension="")
    results = tool(pattern)
    if document_filter:
        results = [result for result in results if document_filter(result)]
    return results


async def grep_documents(
    store: Casebase,
    pattern: str,
    *,
    glob: str | None = None,
    context_lines: int = 0,
    document_filter: DocumentFilter | None = None,
) -> list[GrepMatch]:
    """Search document content with ripgrep and optional filtering."""
    tool = GrepTool(path=_workspace_dir(store))
    matches = await tool(pattern, glob=glob, context_lines=context_lines)
    if document_filter:
        matches = [match for match in matches if document_filter(match.filename)]
    return matches


def semantic_search_documents(
    store: Casebase,
    query: str,
    *,
    type: Literal["dense", "sparse", "hybrid"] = "hybrid",
    top_k: int = 5,
    group_stores: Sequence[Casebase] = (),
    filter_for_store: Callable[[Casebase], DocumentFilter | None] | None = None,
) -> list[RetrievedChunk]:
    """Search document chunks across one or more stores."""
    resolved_filter = filter_for_store or (lambda _store: None)
    return apply_search_tool(
        (store, *group_stores),
        type,
        query,
        top_k,
        filter_for_store=resolved_filter,
    )


def get_document_lines(
    store: Casebase,
    filename: str,
    *,
    start: int = 1,
    end: int | None = None,
    document_filter: DocumentFilter | None = None,
) -> DocumentRange | None:
    """Load a line range from a document."""
    if document_filter and not document_filter(filename):
        return None
    tool = GetDocumentLinesTool(path=_workspace_dir(store))
    return tool(filename, start, end)


def get_document_text(
    store: Casebase,
    filename: str,
    *,
    document_filter: DocumentFilter | None = None,
) -> str | None:
    """Load a full document."""
    if document_filter and not document_filter(filename):
        return None
    tool = GetDocumentTool(path=_workspace_dir(store))
    return tool(filename)


def list_document_chunks(
    store: Casebase,
    filename: str,
    *,
    document_filter: DocumentFilter | None = None,
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document."""
    if document_filter and not document_filter(filename):
        return None

    def _loader(fn: str) -> Sequence[ChunkSummary] | None:
        metadata = load_document_metadata(_metadata_dir(store), fn)
        if not metadata:
            return None
        return [
            ChunkSummary(
                token_count=chunk.token_count,
                start_index=chunk.start_index,
                end_index=chunk.end_index,
            )
            for chunk in metadata.chunks
        ]

    tool = ListChunksTool(loader=_loader)
    return tool(filename)


def get_document_chunk(
    store: Casebase,
    filename: str,
    chunk_index: int,
    *,
    document_filter: DocumentFilter | None = None,
) -> str | None:
    """Load one chunk from document metadata."""
    if document_filter and not document_filter(filename):
        return None

    def _loader(fn: str, idx: int) -> str | None:
        metadata = load_document_metadata(_metadata_dir(store), fn)
        if not metadata:
            return None
        if 0 <= idx < len(metadata.chunks):
            return metadata.chunks[idx].text
        return None

    tool = GetChunkTool(loader=_loader)
    return tool(filename, chunk_index)


async def edit_document_text(
    store: Casebase,
    filename: str,
    old_string: str,
    new_string: str,
    *,
    document_filter: DocumentFilter | None = None,
) -> str:
    """Edit a document and refresh chunks/search state."""
    if document_filter and not document_filter(filename):
        return f"Error: '{filename}' is not accessible."

    async def _on_write(changed_filename: str) -> None:
        await rechunk_document(store, changed_filename)
        mark_dirty(store)

    tool = EditDocumentTool(path=_workspace_dir(store), on_write=_on_write)
    return await tool(filename, old_string, new_string)


async def write_document_text(
    store: Casebase,
    filename: str,
    content: str,
    *,
    mode: Literal["prepend", "append", "replace"] = "replace",
    document_filter: DocumentFilter | None = None,
) -> str:
    """Write a document and refresh chunks/search state."""
    if document_filter and not document_filter(filename):
        return f"Error: '{filename}' is not accessible."

    async def _on_write(changed_filename: str) -> None:
        await rechunk_document(store, changed_filename)
        mark_dirty(store)

    tool = WriteDocumentTool(
        path=_workspace_dir(store),
        extension="",
        on_write=_on_write,
    )
    return await tool(filename, content, mode)
