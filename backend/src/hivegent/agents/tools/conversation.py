"""Conversation-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.messages import ToolReturn

from ...config import settings
from ...messages import (
    list_conversations as _list_conversations,
)
from ...tools import JqTool
from ...tools.base import ToolOutput
from ...tools.pydantic_ai import for_pydantic_ai, wrap_tool_output
from ..common import UserDeps

__all__ = [
    "conversation_toolset",
    "list_conversations",
]

conversation_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)


@conversation_toolset.tool
def list_conversations(
    ctx: RunContext[UserDeps],
) -> ToolReturn:
    """List past conversations with titles, dates, and message counts.

    Returns summaries sorted by most recent first.
    """
    conversations = _list_conversations(ctx.deps.store.id)
    if not conversations:
        formatted = "(no conversations)"
    else:
        formatted = "\n".join(
            f"{c.id[:8]}  {c.updated_at:%Y-%m-%d}  {c.message_count:>3} msgs  {c.title}"
            for c in conversations
        )
    return wrap_tool_output(ToolOutput(data=conversations, formatted=formatted))


def _jq_factory(deps: UserDeps) -> JqTool:
    return JqTool(paths=deps.store.conversations_dir(settings.data_dir))


conversation_toolset.add_function(
    for_pydantic_ai(_jq_factory, UserDeps),
    name="query_conversations",
)
