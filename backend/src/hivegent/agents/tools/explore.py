"""Explore-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ... import tool_runtime
from ...chunkers.base import ChunkSummary, RetrievedChunk
from ...chunks import ChunkIndexArg, GetChunkTool, ListChunksTool
from ...tools import (
    DocumentRange,
    DocumentSummary,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    LanceDBSearchTool,
    ListDocumentsTool,
)
from ...tools.base import tool_description
from ...tools.documents import (
    DocumentEndLineArg,
    DocumentFilenameArg,
    DocumentMaxDepthArg,
    DocumentStartLineArg,
    DocumentSubdirArg,
    GlobPatternArg,
)
from ...tools.grep import ContextLinesArg, GrepGlobArg, GrepPatternArg
from ...tools.retrieval import SearchQueryArg, SearchTopKArg, SearchTypeArg
from ..common import UserDeps

__all__ = [
    "explore_toolset",
    "get_chunk",
    "get_document",
    "get_document_lines",
    "glob_documents",
    "grep",
    "list_chunks",
    "list_documents",
    "semantic_search",
]

explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@explore_toolset.tool(description=tool_description(ListDocumentsTool))
def list_documents(
    ctx: RunContext[UserDeps],
    subdir: DocumentSubdirArg = None,
    max_depth: DocumentMaxDepthArg = None,
) -> list[DocumentSummary]:
    return tool_runtime.list_documents(
        ctx.deps.store,
        subdir=subdir,
        max_depth=max_depth,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GlobDocumentsTool))
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: GlobPatternArg,
) -> list[str]:
    return tool_runtime.glob_documents(
        ctx.deps.store,
        pattern,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GrepTool))
async def grep(
    ctx: RunContext[UserDeps],
    pattern: GrepPatternArg,
    glob: GrepGlobArg = None,
    context_lines: ContextLinesArg = 0,
) -> list[GrepMatch]:
    return await tool_runtime.grep(
        ctx.deps.store,
        pattern,
        glob=glob,
        context_lines=context_lines,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(LanceDBSearchTool))
def semantic_search(
    ctx: RunContext[UserDeps],
    query: SearchQueryArg,
    search_type: SearchTypeArg = "hybrid",
    top_k: SearchTopKArg = 5,
) -> list[RetrievedChunk]:
    return tool_runtime.semantic_search(
        ctx.deps.store,
        query,
        search_type=search_type,
        top_k=top_k,
        group_stores=ctx.deps.group_stores,
        filter_for_store=ctx.deps.filter_for_store,
    )


@explore_toolset.tool(description=tool_description(GetDocumentLinesTool))
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    start: DocumentStartLineArg = 1,
    end: DocumentEndLineArg = None,
) -> DocumentRange | None:
    return tool_runtime.get_document_lines(
        ctx.deps.store,
        filename,
        start=start,
        end=end,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GetDocumentTool))
def get_document(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
) -> str | None:
    return tool_runtime.get_document(
        ctx.deps.store,
        filename,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(ListChunksTool))
def list_chunks(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
) -> list[ChunkSummary] | None:
    return tool_runtime.list_chunks(
        ctx.deps.store,
        filename,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GetChunkTool))
def get_chunk(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    chunk_index: ChunkIndexArg,
) -> str | None:
    return tool_runtime.get_chunk(
        ctx.deps.store,
        filename,
        chunk_index,
        document_filter=ctx.deps.document_filter,
    )
