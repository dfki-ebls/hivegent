"""Memory-oriented agent tool registrations.

The memory is one markdown page per user, read with ``read_memory`` and
changed in place with ``write_memory``, so a save never has to repeat what it
keeps.  :class:`MemoryNote` reports it as a ``read_memory`` call when a
conversation starts.  It is model-written, possibly after reading a web page
or a document, so it reaches the model as a tool result and never as an
instruction.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated

from pydantic import Field
from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext

from ...config import settings
from ...db.memory import load_memory, update_memory
from ...store import User
from ...tools.base import ToolRetry, translate_tool_retry
from ..common import UserDeps

__all__ = ["MemoryNote", "edit_memory", "memory_toolset", "read_memory", "write_memory"]

MemoryContentArg = Annotated[
    str,
    Field(
        description=(
            "The markdown to append, or to put in place of `old_text`. "
            "Empty with `old_text` to delete that passage."
        )
    ),
]
MemoryOldTextArg = Annotated[
    str | None,
    Field(
        min_length=1,
        description="A passage of the memory to replace, copied exactly. Omit to append.",
    ),
]

memory_toolset: FunctionToolset[UserDeps] = FunctionToolset()


async def _read(user: User) -> str:
    return await load_memory(user) or "Your memory is empty."


def edit_memory(memory: str, content: str, old_text: str | None, max_chars: int) -> str:
    """Append *content* to *memory*, or put it in place of *old_text*.

    The result may exceed *max_chars* only while it shrinks the memory, so an
    oversized memory can still be condensed.

    Raises:
        ToolRetry: If there is nothing to append, *old_text* does not match
            exactly one passage, or the result does not fit *max_chars*.

    Examples:
        >>> edit_memory("- tea", "- green", None, 100)
        '- tea\\n- green'
        >>> edit_memory("- tea\\n- green", "- black", "- green", 100)
        '- tea\\n- black'
    """
    if old_text is None:
        if not content.strip():
            raise ToolRetry("`content` is empty, so there is nothing to append.")

        updated = "\n".join(text for text in (memory.rstrip(), content.strip()) if text)

    else:
        occurrences = memory.count(old_text)

        if occurrences != 1:
            found = "does not occur" if not occurrences else f"occurs {occurrences} times"

            raise ToolRetry(
                f"`old_text` {found} in the memory, but must match exactly one "
                "passage. Copy it from the memory verbatim, with enough context "
                "to be unique."
            )

        updated = memory.replace(old_text, content, 1).strip()

    if len(updated) > max_chars and len(updated) >= len(memory):
        raise ToolRetry(
            f"The memory would hold {len(updated)} characters, but at most "
            f"{max_chars} fit. Condense it by replacing passages with shorter ones."
        )

    return updated


@memory_toolset.tool
async def read_memory(ctx: RunContext[UserDeps]) -> str:
    """Read your persistent memory, which is preserved across conversations."""
    return await _read(ctx.deps.user)


@memory_toolset.tool
async def write_memory(
    ctx: RunContext[UserDeps],
    content: MemoryContentArg,
    old_text: MemoryOldTextArg = None,
) -> str:
    """Change your persistent memory, which is preserved across conversations.

    Without `old_text`, `content` is appended as new lines.  With it, the one
    passage matching `old_text` exactly is replaced by `content`.
    """
    max_chars = settings.limits.max_memory_chars
    change = partial(edit_memory, content=content, old_text=old_text, max_chars=max_chars)

    with translate_tool_retry(ModelRetry):
        updated = await update_memory(ctx.deps.user, change)

    return f"Memory saved, it holds {len(updated)} of {max_chars} characters."


@dataclass(slots=True)
class MemoryNote(AbstractCapability[UserDeps]):
    """Report the memory as a ``read_memory`` call when a conversation starts.

    The pair is enqueued into the conversation's first request and persisted
    with it, so the memory the conversation started with stays in the cached
    prefix, while a later change reaches the model through its own
    ``write_memory`` call or a fresh read.
    """

    async def before_model_request(
        self, ctx: RunContext[UserDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Enqueue the read before the conversation's first response."""
        if not any(isinstance(message, ModelResponse) for message in ctx.messages):
            call = ToolCallPart(read_memory.__name__, {})
            result = ToolReturnPart(
                call.tool_name, await _read(ctx.deps.user), call.tool_call_id
            )
            ctx.enqueue(ModelResponse(parts=[call]), ModelRequest(parts=[result]))

        return request_context
