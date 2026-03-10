"""Web-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...tools import WebFetch, WebSearch
from ..common import UserDeps
from ...tools.pydantic_ai import register_agent_tools

__all__ = ["web_toolset"]


def _web_search(_deps: UserDeps) -> WebSearch:
    return WebSearch()


def _web_fetch(_deps: UserDeps) -> WebFetch:
    return WebFetch()


web_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(
    web_toolset,
    UserDeps,
    [
        _web_search,
        _web_fetch,
    ],
)
