"""Conversation-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolReturn

from ...db.conversations import (
    list_conversations as _list_conversations,
    load_conversation as _load_conversation,
)
from ...tools.base import ToolOutput
from ...tools.pydantic_ai import wrap_tool_output
from ..common import UserDeps

__all__ = [
    "conversation_toolset",
    "get_conversation",
    "list_conversations",
]

conversation_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)


@conversation_toolset.tool
async def list_conversations(
    ctx: RunContext[UserDeps],
) -> ToolReturn:
    """List past conversations with titles and dates.

    Returns summaries sorted by most recent first.
    """
    conversations = await _list_conversations(ctx.deps.store.id)
    if not conversations:
        formatted = "(no conversations)"
    else:
        formatted = "\n".join(
            f"{c.id[:8]}  {c.updated_at:%Y-%m-%d}  {c.title}" for c in conversations
        )
    return wrap_tool_output(
        ToolOutput(data=conversations, formatted=formatted),
        tool_call_id=ctx.tool_call_id,
    )


@conversation_toolset.tool
async def get_conversation(
    ctx: RunContext[UserDeps],
    conversation_id: str,
) -> ToolReturn:
    """Load a past conversation's full content for analysis.

    Returns the conversation header plus messages so the LLM can scan
    them with its own filtering rather than a jq query.
    """
    conv = await _load_conversation(ctx.deps.store.id, conversation_id)
    if conv is None:
        raise ModelRetry(f"conversation '{conversation_id}' not found.")
    return wrap_tool_output(
        ToolOutput(data=conv, formatted=conv.title or "(untitled)"),
        tool_call_id=ctx.tool_call_id,
    )
