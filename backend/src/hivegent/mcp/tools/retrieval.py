"""Retrieval-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from ...agents import (
    SUBAGENT_CAPABILITIES,
    UserDeps,
    turn_usage_limits,
    user_agent,
)
from ...chunkers.base import RetrievedChunk
from ...config import settings
from ...llm import model_from_config
from ...llm_config import LlmConfig, resolve_llm_config
from ...retrieval import build_search_tool
from ...store import Casebase
from ...tools import VectorSearchTool
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


def _search(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> VectorSearchTool[RetrievedChunk]:
    return build_search_tool((store, *group_stores))


register_mcp_tools(
    mcp_app,
    [
        _search,
    ],
)


@mcp_app.tool()
async def explore_documents(
    task: ExploreTaskArg,
    user_id: str = Depends(get_mcp_user_id),
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> str:
    """Explore documents with the configured subagent."""
    if not settings.llm.model:
        raise ToolError("Document exploration requires a configured LLM model.")

    result = await user_agent.run(
        task,
        model=model_from_config(resolve_llm_config(LlmConfig(), tier="main")),
        deps=UserDeps(
            user_id=user_id,
            store=store,
            group_stores=group_stores,
        ),
        capabilities=[SUBAGENT_CAPABILITIES["documents"]],
        usage_limits=turn_usage_limits,
    )
    return result.output
