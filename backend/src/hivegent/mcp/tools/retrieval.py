"""Retrieval-oriented MCP tool registrations."""

from fastmcp import Context
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ... import tool_runtime
from ...agents import UserDeps, explore_toolset, user_agent
from ...chunkers.base import ChunkSummary, RetrievedChunk
from ...chunks import ChunkIndexArg, GetChunkTool, ListChunksTool
from ...config import settings
from ...prompts import EXPLORE_INSTRUCTIONS
from ...retrieval import build_search_tool
from ...store import Casebase
from ...tools import (
    GetDocumentLinesTool,
    GlobDocumentsTool,
    GrepTool,
    LanceDBSearchTool,
    ListDocumentsTool,
)
from ...tools.base import tool_description
from ...tools.documents import DocumentFilenameArg
from ...tools.retrieval import SearchQueryArg, SearchTopKArg, SearchTypeArg
from ..app import mcp_app
from ..common import (
    ExploreTaskArg,
    get_mcp_group_stores,
    get_mcp_user_id,
    get_mcp_user_store,
)

__all__ = [
    "explore_documents",
    "get_chunk",
    "list_chunks",
    "semantic_search",
]


@mcp_app.tool(description=tool_description(LanceDBSearchTool))
def semantic_search(
    query: SearchQueryArg,
    search_type: SearchTypeArg = "hybrid",
    top_k: SearchTopKArg = 5,
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> list[RetrievedChunk]:
    return tool_runtime.semantic_search(
        store,
        query,
        search_type=search_type,
        top_k=top_k,
        group_stores=group_stores,
    )


@mcp_app.tool(description=tool_description(ListChunksTool))
def list_chunks(
    filename: DocumentFilenameArg,
    store: Casebase = Depends(get_mcp_user_store),
) -> list[ChunkSummary] | None:
    return tool_runtime.list_chunks(store, filename)


@mcp_app.tool(description=tool_description(GetChunkTool))
def get_chunk(
    filename: DocumentFilenameArg,
    chunk_index: ChunkIndexArg,
    store: Casebase = Depends(get_mcp_user_store),
) -> str | None:
    return tool_runtime.get_chunk(store, filename, chunk_index)


@mcp_app.tool()
async def explore_documents(
    task: ExploreTaskArg,
    ctx: Context,
    user_id: str = Depends(get_mcp_user_id),
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> str | None:
    """Explore documents with a subagent or MCP sampling fallback."""
    model_name = settings.llm.small_model or settings.llm.model

    if model_name:
        result = await user_agent.run(
            task,
            model=OpenAIResponsesModel(
                model_name,
                provider=OpenAIProvider(
                    api_key=settings.llm.api_key,
                    base_url=settings.llm.base_url or None,
                ),
            ),
            deps=UserDeps(
                user_id=user_id,
                store=store,
                group_stores=group_stores,
            ),
            toolsets=[explore_toolset],
            instructions=EXPLORE_INSTRUCTIONS,
        )
        return result.output

    workspace = store.workspace_dir(settings.data_dir)
    all_stores = (store, *group_stores)
    result = await ctx.sample(
        task,
        system_prompt=EXPLORE_INSTRUCTIONS,
        tools=[
            ListDocumentsTool(path=workspace, extension=""),
            GlobDocumentsTool(path=workspace, extension=""),
            GrepTool(path=workspace),
            build_search_tool(all_stores),
            GetDocumentLinesTool(path=workspace),
        ],
    )
    return result.text
