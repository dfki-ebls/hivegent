"""Conversation-oriented agent tool registrations."""

import reprlib
from collections.abc import Iterator, Mapping

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    ToolCallPart,
    ToolReturn,
    UserPromptPart,
)

from ...db.conversations import ConversationData, ConversationSummary
from ...db.conversations import (
    list_conversations as _list_conversations,
)
from ...db.conversations import (
    load_conversation as _load_conversation,
)
from ...tools.base import ToolOutput
from ...tools.formatting import BLOCK_SEP, truncate_line
from ...tools.pydantic_ai import wrap_tool_output
from ..common import UserDeps

__all__ = [
    "conversation_toolset",
    "format_conversation",
    "get_conversation",
    "list_conversations",
]

# Registered eagerly like every other tool, and withheld by
# `settings.tools.disabled`, which lists both of these by default: no
# instruction block names them, a chat turn about the documents never reaches
# them, and the `explore` tool already offers the same history under its
# `conversations` scope.  A deployment that wants them back drops them from
# that list, which is a decision an operator can make and tool search could
# not.
conversation_toolset: FunctionToolset[UserDeps] = FunctionToolset()


_MAX_TOOL_CALL_CHARS = 200
"""Cap on one rendered tool call, which names what was done, not its result."""

_ARG_REPR = reprlib.Repr(maxlevel=2, maxstring=_MAX_TOOL_CALL_CHARS)
"""Renders one argument value cut short, never the whole of a large one."""


def _render_args(args: str | Mapping[str, object] | None) -> str:
    """The arguments of a tool call, rendered no longer than they are shown.

    A large argument (a written document, a program) is cut while rendering
    rather than serialized whole and cut afterwards.
    """
    if isinstance(args, str):
        return args[:_MAX_TOOL_CALL_CHARS]

    return ", ".join(
        f"{key}={_ARG_REPR.repr(value)}" for key, value in (args or {}).items()
    )


def _header(conversation: ConversationData | ConversationSummary) -> str:
    """The `id  date  title` line naming *conversation*."""
    return (
        f"{conversation.id}  {conversation.updated_at:%Y-%m-%d}  "
        f"{conversation.title or '(untitled)'}"
    )


def _render_parts(message: ModelMessage) -> Iterator[str]:
    """The readable blocks of *message*: dialogue text and the tools it called.

    Tool returns, system prompts, and reasoning are left out, since they repeat
    workspace content or internals rather than what was said and done.
    """
    for part in message.parts:
        match part:
            case UserPromptPart(content=content):
                text = (
                    content
                    if isinstance(content, str)
                    else "\n".join(
                        item if isinstance(item, str) else "[attachment]"
                        for item in content
                    )
                )
                yield f"user:\n{text.strip()}"

            case TextPart(content=content) if content.strip():
                yield f"assistant:\n{content.strip()}"

            case ToolCallPart():
                call = f"{part.tool_name}({_render_args(part.args)})"
                yield f"tool call: {truncate_line(call, _MAX_TOOL_CALL_CHARS)}"

            case _:
                pass


def format_conversation(conversation: ConversationData) -> str:
    """Render *conversation* as its header followed by one block per message part.

    The header matches a :func:`list_conversations` line.
    """
    header = _header(conversation)
    blocks = [
        block for message in conversation.messages for block in _render_parts(message)
    ]

    if not blocks:
        return f"{header}\n\n(no messages)"

    return BLOCK_SEP.join([header, *blocks])


@conversation_toolset.tool
async def list_conversations(
    ctx: RunContext[UserDeps],
) -> ToolReturn:
    """List past conversations as `id  date  title`, most recent first.

    Metadata only: no message content is returned.  Pass an `id` from this
    listing verbatim to `get_conversation` to open a conversation.
    """
    conversations = await _list_conversations(ctx.deps.store.id)
    formatted = "\n".join(map(_header, conversations)) or "(no conversations)"

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
        ToolOutput(data=conv, formatted=format_conversation(conv)),
        tool_call_id=ctx.tool_call_id,
    )
