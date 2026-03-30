"""Conversation-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ...config import settings
from ...messages import (
    ConversationSummary,
    list_conversations as _list_conversations,
)
from ...tools import JqTool
from ..common import UserDeps
from ...tools.pydantic_ai import for_pydantic_ai

__all__ = [
    "conversation_toolset",
    "list_conversations_tool",
]

conversation_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@conversation_toolset.tool
def list_conversations_tool(
    ctx: RunContext[UserDeps],
) -> list[ConversationSummary]:
    """List past conversations with titles, dates, and message counts.

    Returns summaries sorted by most recent first.
    """
    return _list_conversations(ctx.deps.store.id)


def _jq_factory(deps: UserDeps) -> JqTool:
    return JqTool(paths=deps.store.conversations_dir(settings.data_dir))


conversation_toolset.add_function(
    for_pydantic_ai(_jq_factory, UserDeps),
    name="query_conversations",
)
