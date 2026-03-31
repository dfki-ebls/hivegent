"""Retrieval-oriented MCP tool registrations."""

from fastmcp import Context
from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...agents.compact import CompactToolResultModel

from ...agents import UserDeps, explore_toolset, user_agent
from ...chunkers.base import RetrievedChunk
from ...chunks import GetChunkTool, ListChunksTool
from ...config import settings
from ...prompts import EXPLORE_INSTRUCTIONS, join_instructions
from ...retrieval import build_search_tool
from ...store import Casebase, build_search_paths
from ...tools import (
    GetDocumentLinesTool,
    GlobDocumentsTool,
    GrepTool,
    LanceDBSearchTool,
    ListDocumentsTool,
)
from ...tools.fastmcp import register_mcp_tools
from ..app import mcp_app
from ..common import (
    ExploreTaskArg,
    get_mcp_group_stores,
    get_mcp_user_id,
    get_mcp_user_store,
)

__all__ = [
    "explore_documents",
]


def _list_chunks(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ListChunksTool:
    return ListChunksTool(
        paths=build_search_paths(
            store, group_stores, settings.data_dir, dir_fn=Casebase.metadata_dir
        )
    )


def _get_chunk(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> GetChunkTool:
    return GetChunkTool(
        paths=build_search_paths(
            store, group_stores, settings.data_dir, dir_fn=Casebase.metadata_dir
        )
    )


def _semantic_search(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> LanceDBSearchTool[RetrievedChunk]:
    return build_search_tool((store, *group_stores))


register_mcp_tools(
    mcp_app,
    [
        _list_chunks,
        _get_chunk,
        _semantic_search,
    ],
)


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
            model=CompactToolResultModel(
                OpenAIResponsesModel(
                    model_name,
                    provider=OpenAIProvider(
                        api_key=settings.llm.api_key,
                        base_url=settings.llm.base_url or None,
                    ),
                )
            ),
            deps=UserDeps(
                user_id=user_id,
                store=store,
                group_stores=group_stores,
            ),
            toolsets=[explore_toolset],
            instructions=join_instructions([EXPLORE_INSTRUCTIONS]),
        )
        return result.output

    paths = build_search_paths(store, group_stores, settings.data_dir)
    all_stores = (store, *group_stores)
    result = await ctx.sample(
        task,
        system_prompt=join_instructions([EXPLORE_INSTRUCTIONS]),
        tools=[
            ListDocumentsTool(paths=paths),
            GlobDocumentsTool(paths=paths),
            GrepTool(paths=paths),
            build_search_tool(all_stores),
            GetDocumentLinesTool(paths=paths),
        ],
    )
    return result.text
