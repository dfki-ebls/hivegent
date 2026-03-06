"""Web-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ...tools import WebFetch, WebSearch
from ...tools.base import tool_description
from ...tools.web import WebMaxResultsArg, WebQueryArg, WebUrlArg
from ..common import UserDeps

__all__ = ["web_fetch", "web_search", "web_toolset"]

web_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@web_toolset.tool(description=tool_description(WebSearch))
def web_search(
    _ctx: RunContext[UserDeps],
    query: WebQueryArg,
    max_results: WebMaxResultsArg = 5,
) -> list[dict[str, str]]:
    tool = WebSearch()
    return tool(query, max_results)


@web_toolset.tool(description=tool_description(WebFetch))
async def web_fetch(
    _ctx: RunContext[UserDeps],
    url: WebUrlArg,
) -> str:
    tool = WebFetch()
    return await tool(url)
