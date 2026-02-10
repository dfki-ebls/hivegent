"""RAG agent with document retrieval tools."""

from dataclasses import dataclass

from pydantic_ai import Agent, FunctionToolset, RunContext

from . import tools
from .config import settings
from .tools import DocumentFilter
from .types import (
    ChunkSummary,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
    RetrievedDocument,
)

__all__ = ["UserDeps", "base_agent", "rag_toolset", "user_agent"]


@dataclass(slots=True, frozen=True)
class UserDeps:
    """Dependencies for user-specific agent operations."""

    user_id: str
    document_filter: DocumentFilter | None = None


base_agent: Agent[None, str] = Agent()
user_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps)

rag_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@rag_toolset.tool
def list_documents(
    ctx: RunContext[UserDeps],
    subdir: str | None = None,
    max_depth: int | None = None,
) -> list[DocumentSummary]:
    """List all available documents with their sizes in bytes.

    Args:
        subdir: Only include documents under this subdirectory.
        max_depth: Maximum nesting depth relative to *subdir* (or root).
    """
    return tools.ListDocumentsTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(subdir=subdir, max_depth=max_depth)


@rag_toolset.tool
def get_document(ctx: RunContext[UserDeps], filename: str) -> str | None:
    """Get the full content of a specific document.

    Args:
        filename: The relative path to retrieve (e.g. "report.md" or "projects/report.md").
    """
    return tools.GetDocumentTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(filename)


@rag_toolset.tool
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: str,
    start: int = 1,
    end: int | None = None,
) -> DocumentRange | None:
    """Get a range of lines from a document.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
        start: First line to include (1-indexed, default: 1).
        end: Last line to include (1-indexed, default: end of file).
    """
    return tools.GetDocumentLinesTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(filename, start, end)


@rag_toolset.tool
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: str,
) -> list[str]:
    """Find documents matching a glob pattern.

    Args:
        pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
    """
    return tools.GlobDocumentsTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(pattern)


@rag_toolset.tool
def grep(
    ctx: RunContext[UserDeps],
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    include_content: bool = True,
) -> list[GrepMatch]:
    """Search documents for a pattern.

    Uses smart case matching: case-insensitive unless the pattern contains
    uppercase letters.

    Args:
        pattern: Text or regex pattern to search for.
        glob: Only search files matching this pattern (e.g., "*.md", "notes/*").
        context_lines: Number of lines to show before and after each match.
        include_content: Whether to include the matching line content.
    """
    return tools.GrepTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(
        pattern,
        glob=glob,
        context_lines=context_lines,
        include_content=include_content,
    )


@rag_toolset.tool
def search_documents(
    ctx: RunContext[UserDeps],
    query: str,
    top_k: int = 3,
    subdir: str | None = None,
    max_depth: int | None = None,
) -> list[RetrievedDocument]:
    """Semantic search for documents using BM25 ranking.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.
        subdir: Only include documents under this subdirectory.
        max_depth: Maximum nesting depth relative to *subdir* (or root).
    """
    return tools.SearchDocumentsTool(
        path=settings.get_user_documents_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(query, top_k, subdir=subdir, max_depth=max_depth)


@rag_toolset.tool
def list_chunks(
    ctx: RunContext[UserDeps],
    filename: str,
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
    """
    return tools.ListChunksTool(
        path=settings.get_user_chunks_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(filename)


@rag_toolset.tool
def get_chunk(
    ctx: RunContext[UserDeps],
    filename: str,
    chunk_index: int,
) -> str | None:
    """Get the text content of a specific chunk.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
        chunk_index: The index of the chunk to retrieve.
    """
    return tools.GetChunkTool(
        path=settings.get_user_chunks_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(
        filename,
        chunk_index,
    )


@rag_toolset.tool
def search_chunks(
    ctx: RunContext[UserDeps],
    query: str,
    top_k: int = 5,
    subdir: str | None = None,
    max_depth: int | None = None,
) -> list[RetrievedChunk]:
    """Search across all document chunks using BM25 ranking.

    Returns the most relevant chunks from all chunked documents.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.
        subdir: Only include chunks from documents under this subdirectory.
        max_depth: Maximum nesting depth relative to *subdir* (or root).
    """
    return tools.SearchChunksTool(
        path=settings.get_user_chunks_dir(ctx.deps.user_id),
        document_filter=ctx.deps.document_filter,
    )(query, top_k, subdir=subdir, max_depth=max_depth)
