"""Conversation-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ...config import settings
from ...messages import (
    ConversationSummary,
    list_conversations as _list_conversations,
)
from ...tools import JqTool
from ...tools.jq import JqFilenameArg, JqFilterArg
from ..common import UserDeps

__all__ = [
    "conversation_toolset",
    "list_conversations_tool",
    "query_conversations",
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


@conversation_toolset.tool
async def query_conversations(
    ctx: RunContext[UserDeps],
    filter: JqFilterArg,
    filename: JqFilenameArg,
) -> str:
    """Run a jq filter on a conversation JSON file.

    Each conversation file has this schema::

        {
            "title": str,
            "created_at": str (ISO datetime),
            "updated_at": str (ISO datetime),
            "messages": [
                {
                    "kind": "request" | "response",
                    "parts": [{"part_kind": "user-prompt", "content": str}, ...]
                },
                ...
            ]
        }

    Example filters:

    - ``.title`` -- get the conversation title.
    - ``.messages[].parts[] | select(.content | test("deadline"))``
      -- search message content for "deadline".
    """
    tool = JqTool(
        path=ctx.deps.store.conversations_dir(settings.data_dir),
    )
    return await tool(filter, filename)
