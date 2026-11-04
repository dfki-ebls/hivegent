"""Conversation-oriented agent tool registrations."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    ToolCallPart,
    ToolReturn,
    UserPromptPart,
)

from ...db.conversations import (
    ConversationData,
    ConversationSummary,
    owned_conversation,
    render_prompt,
)
from ...db.conversations import (
    list_conversations as _list_conversations,
)
from ...db.conversations import (
    load_conversation as _load_conversation,
)
from ...db.conversations import (
    search_conversation as _search_conversation,
)
from ...tools.base import ToolOutput
from ...tools.formatting import BLOCK_SEP, render_arguments
from ...tools.pydantic_ai import wrap_tool_output
from ..common import UserDeps, agent_toolset

__all__ = [
    "ConversationMatch",
    "conversation_toolset",
    "format_conversation",
    "format_matches",
    "format_messages",
    "get_conversation",
    "list_conversations",
    "search_conversation",
]

# Registered eagerly like every other tool, and withheld by
# `settings.tools.disabled`, which lists all of these by default: no
# instruction block names them, a chat turn about the documents never reaches
# them, and the `explore` tool already offers the same history under its
# `conversations` scope.  A deployment that wants them back drops them from
# that list, which is a decision an operator can make and tool search could
# not.
conversation_toolset: FunctionToolset[UserDeps] = agent_toolset()


_MAX_ARGUMENT_CHARS = 200
"""Cap on the arguments of one rendered tool call, which names what was done, not its result."""

_MAX_MATCHES = 20

QueryArg = Annotated[
    str,
    Field(
        description=(
            "Keywords that must all occur in a match, compared by word stem, "
            "so pass a few distinctive words rather than a question."
        ),
        min_length=1,
    ),
]
ConversationIdArg = Annotated[
    str | None,
    Field(
        description=(
            "A conversation `id` from `list_conversations`, or null for the "
            "current conversation."
        ),
    ),
]
LimitArg = Annotated[
    int,
    Field(description="Maximum number of matches to return.", ge=1, le=_MAX_MATCHES),
]


@dataclass(frozen=True, slots=True)
class ConversationMatch:
    """A passage of a conversation that matched a search."""

    position: int
    """The position of the passage's message in the conversation, from 0."""

    text: str


def _header(conversation: ConversationData | ConversationSummary) -> str:
    """The `id  date  title` line naming *conversation*."""
    return (
        f"{conversation.id}  {conversation.updated_at:%Y-%m-%d}  "
        f"{conversation.title or '(untitled)'}"
    )


def _render_parts(message: ModelMessage) -> Iterator[str]:
    """The readable blocks of *message*: dialogue text and the tools it called.

    Tool returns, system prompts, reasoning, and instruction changes are left out,
    since they repeat workspace content or internals rather than what was said
    and done.
    """
    for part in message.parts:
        match part:
            case UserPromptPart():
                yield f"user:\n{render_prompt(part).strip()}"

            case TextPart(content=content) if content.strip():
                yield f"assistant:\n{content.strip()}"

            case ToolCallPart():
                arguments = render_arguments(part.args, _MAX_ARGUMENT_CHARS)
                yield f"tool call: {part.tool_name}({arguments})"

            case _:
                pass


def format_conversation(conversation: ConversationData) -> str:
    """Render *conversation* as its header followed by one block per message part.

    The header matches a :func:`list_conversations` line.
    """
    return BLOCK_SEP.join(
        [_header(conversation), format_messages(conversation.messages)]
    )


def format_messages(messages: Iterable[ModelMessage]) -> str:
    """Render *messages* as one block per message part, dialogue and tool calls."""
    blocks = [block for message in messages for block in _render_parts(message)]

    return BLOCK_SEP.join(blocks) or "(no messages)"


def format_matches(matches: Iterable[ConversationMatch]) -> str:
    """Render *matches* as one block each, prefixed by its message's position."""
    blocks = [f"[{match.position}] {match.text}" for match in matches]

    return BLOCK_SEP.join(blocks) or "(no matches)"


@conversation_toolset.tool
async def list_conversations(
    ctx: RunContext[UserDeps],
) -> ToolReturn:
    """List past conversations as `id  date  title`, most recent first.

    Metadata only: no message content is returned.  Pass an `id` from this
    listing verbatim to `get_conversation` to open a conversation.
    """
    conversations = await _list_conversations(ctx.deps.user)
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
    owned = await owned_conversation(ctx.deps.user, conversation_id)
    conv = None if owned is None else await _load_conversation(owned)

    if conv is None:
        raise ModelRetry(f"conversation '{conversation_id}' not found.")

    return wrap_tool_output(
        ToolOutput(data=conv, formatted=format_conversation(conv)),
        tool_call_id=ctx.tool_call_id,
    )


@conversation_toolset.tool
async def search_conversation(
    ctx: RunContext[UserDeps],
    query: QueryArg,
    conversation_id: ConversationIdArg = None,
    limit: LimitArg = 5,
) -> ToolReturn:
    """Search a conversation's stored messages by keywords, best match first.

    Recovers what an earlier turn said or did, including turns a
    conversation summary replaced in context.  Each match is one user
    prompt, assistant text, or tool call, prefixed by the `[position]` of
    its message, and the turn in progress is not stored yet.
    """
    target = conversation_id or ctx.conversation_id

    if target is None:
        raise ModelRetry("no current conversation, pass a `conversation_id`.")

    owned = await owned_conversation(ctx.deps.user, target)

    if owned is None:
        raise ModelRetry(f"conversation '{target}' not found.")

    found = await _search_conversation(owned, query, _render_parts, limit=limit)
    matches = [ConversationMatch(position, text) for position, text in found]

    return wrap_tool_output(
        ToolOutput(data=matches, formatted=format_matches(matches)),
        tool_call_id=ctx.tool_call_id,
    )
