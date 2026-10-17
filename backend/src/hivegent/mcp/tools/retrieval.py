"""Retrieval-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from ...agents import (
    SUBAGENT_CAPABILITIES,
    UserDeps,
    explore_agent,
    turn_usage_limits,
)
from ...chunkers.base import RetrievedChunk
from ...config import settings
from ...llm import model_from_config
from ...llm_config import LlmConfig, resolve_llm_config
from ...retrieval import build_search_tool
from ...store import User
from ...tools import VectorSearchTool
from ...tools.fastmcp import register_mcp_tools
from ..app import mcp_app
from ..common import ExploreTaskArg, get_mcp_user

__all__ = [
    "explore_documents",
]


def _search(user: User = Depends(get_mcp_user)) -> VectorSearchTool[RetrievedChunk]:
    return build_search_tool(user.stores())


register_mcp_tools(
    mcp_app, [_search], max_chars=settings.llm.tool_output_max_chars
)


@mcp_app.tool()
async def explore_documents(
    task: ExploreTaskArg,
    user: User = Depends(get_mcp_user),
) -> str:
    """Explore documents with the configured subagent."""
    if not settings.llm.model:
        raise ToolError("Document exploration requires a configured LLM model.")

    result = await explore_agent.run(
        task,
        model=model_from_config(resolve_llm_config(LlmConfig(), tier="main")),
        deps=UserDeps(user=user, mode="read"),
        capabilities=[SUBAGENT_CAPABILITIES["documents"]],
        usage_limits=turn_usage_limits,
    )
    return result.output
