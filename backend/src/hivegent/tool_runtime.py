"""Shared runtime helpers for document-oriented agent and MCP tools."""

from collections.abc import Callable, Sequence
from pathlib import Path

from .chunks import (
    ChunkIndexArg,
    GetChunkTool,
    ListChunksTool,
    rechunk_document,
)
from .chunkers.base import ChunkSummary, RetrievedChunk
from .config import settings
from .retrieval import apply_search_tool, mark_dirty
from .store import Casebase
from .tools import (
    DocumentRange,
    DocumentSummary,
    EditDocumentTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    ListDocumentsTool,
    WriteDocumentTool,
)
from .tools.documents import (
    DocumentEndLineArg,
    DocumentFilenameArg,
    DocumentMaxDepthArg,
    DocumentStartLineArg,
    DocumentSubdirArg,
    GlobPatternArg,
)
from .tools.grep import ContextLinesArg, GrepGlobArg, GrepPatternArg
from .tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    WriteModeArg,
)
from .tools.retrieval import SearchQueryArg, SearchTopKArg, SearchTypeArg
from .types import DocumentFilter

__all__ = [
    "edit_document",
    "get_chunk",
    "get_document_lines",
    "get_document",
    "glob_documents",
    "grep",
    "list_chunks",
    "list_documents",
    "semantic_search",
    "write_document",
]


def _workspace_dir(store: Casebase) -> Path:
    return store.workspace_dir(settings.data_dir)


def _metadata_dir(store: Casebase) -> Path:
    return store.metadata_dir(settings.data_dir)


def list_documents(
    store: Casebase,
    *,
    subdir: DocumentSubdirArg = None,
    max_depth: DocumentMaxDepthArg = None,
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
    pattern: GlobPatternArg,
    *,
    document_filter: DocumentFilter | None = None,
) -> list[str]:
    """Expand a glob pattern within a store."""
    tool = GlobDocumentsTool(
        path=_workspace_dir(store),
        extension="",
    )
    results = tool(pattern)
    if document_filter:
        results = [result for result in results if document_filter(result)]
    return results


async def grep(
    store: Casebase,
    pattern: GrepPatternArg,
    *,
    glob: GrepGlobArg = None,
    context_lines: ContextLinesArg = 0,
    document_filter: DocumentFilter | None = None,
) -> list[GrepMatch]:
    """Search document content with ripgrep and optional filtering."""
    tool = GrepTool(path=_workspace_dir(store))
    matches = await tool(pattern, glob=glob, context_lines=context_lines)
    if document_filter:
        matches = [match for match in matches if document_filter(match.filename)]
    return matches


def semantic_search(
    store: Casebase,
    query: SearchQueryArg,
    *,
    search_type: SearchTypeArg = "hybrid",
    top_k: SearchTopKArg = 5,
    group_stores: Sequence[Casebase] = (),
    filter_for_store: Callable[[Casebase], DocumentFilter | None] | None = None,
) -> list[RetrievedChunk]:
    """Search document chunks across one or more stores."""
    resolved_filter = filter_for_store or (lambda _store: None)
    return apply_search_tool(
        (store, *group_stores),
        search_type,
        query,
        top_k,
        filter_for_store=resolved_filter,
    )


def get_document_lines(
    store: Casebase,
    filename: DocumentFilenameArg,
    *,
    start: DocumentStartLineArg = 1,
    end: DocumentEndLineArg = None,
    document_filter: DocumentFilter | None = None,
) -> DocumentRange | None:
    """Load a line range from a document."""
    if document_filter and not document_filter(filename):
        return None
    tool = GetDocumentLinesTool(path=_workspace_dir(store))
    return tool(filename, start, end)


def get_document(
    store: Casebase,
    filename: DocumentFilenameArg,
    *,
    document_filter: DocumentFilter | None = None,
) -> str | None:
    """Load a full document."""
    if document_filter and not document_filter(filename):
        return None
    tool = GetDocumentTool(path=_workspace_dir(store))
    return tool(filename)


def list_chunks(
    store: Casebase,
    filename: DocumentFilenameArg,
    *,
    document_filter: DocumentFilter | None = None,
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document."""
    if document_filter and not document_filter(filename):
        return None
    tool = ListChunksTool(metadata_dir=_metadata_dir(store))
    return tool(filename)


def get_chunk(
    store: Casebase,
    filename: DocumentFilenameArg,
    chunk_index: ChunkIndexArg,
    *,
    document_filter: DocumentFilter | None = None,
) -> str | None:
    """Load one chunk from document metadata."""
    if document_filter and not document_filter(filename):
        return None
    tool = GetChunkTool(metadata_dir=_metadata_dir(store))
    return tool(filename, chunk_index)


async def edit_document(
    store: Casebase,
    filename: DocumentFilenameArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
    *,
    document_filter: DocumentFilter | None = None,
) -> str:
    """Edit a document and refresh chunks/search state."""
    if document_filter and not document_filter(filename):
        return f"Error: '{filename}' is not accessible."

    async def _on_write(filename: str) -> None:
        await rechunk_document(store, filename)
        mark_dirty(store)

    tool = EditDocumentTool(
        path=_workspace_dir(store),
        on_write=_on_write,
    )
    return await tool(filename, old_string, new_string)


async def write_document(
    store: Casebase,
    filename: DocumentFilenameArg,
    content: DocumentContentArg,
    *,
    mode: WriteModeArg = "replace",
    document_filter: DocumentFilter | None = None,
) -> str:
    """Write a document and refresh chunks/search state."""
    if document_filter and not document_filter(filename):
        return f"Error: '{filename}' is not accessible."

    async def _on_write(filename: str) -> None:
        await rechunk_document(store, filename)
        mark_dirty(store)

    tool = WriteDocumentTool(
        path=_workspace_dir(store),
        extension="",
        on_write=_on_write,
    )
    return await tool(filename, content, mode)
