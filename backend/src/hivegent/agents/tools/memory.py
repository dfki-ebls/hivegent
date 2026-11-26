"""Memory-oriented agent tool registrations.

The memory is one markdown page per user, read with ``read_memory`` and
changed in place with ``write_memory``, so a save never has to repeat what it
keeps.  :data:`MEMORY_NOTE` reports it as a ``read_memory`` call when a
conversation starts, and again after each compaction.  It is model-written, possibly after reading a web page
or a document, so it reaches the model as a tool result and never as an
instruction, and every write asks the user first, whatever the mode, since a
planted instruction saved there would reach every later conversation.  That
is the host policy for a write outside the changeset gateway
(``agents/policy.py``), which ``guards.HOST_GATE`` applies before the tool
writes :func:`~hivegent.db.memory.user_memory`.  So a write lands only once the
user approved the call, and the tool refuses a change that would not land
before the user is asked.
"""

from collections.abc import Sequence
from functools import partial
from typing import Annotated

from exar.errors import Conflict
from exar.pydantic_ai import Read, ToolNote, latest_read
from exar.text import replace_once
from exar.tools import Retry, effect, retry_on
from exar.typing import Changes, Update
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage

from ...config import settings
from ...db.conversations import HIDDEN_KEY
from ...db.memory import memory_fits, user_memory
from ...store import User
from ...tools.base import ToolFunc
from ..common import AgentTools, Hooked, UserDeps

__all__ = [
    "MEMORY_NOTE",
    "edit_memory",
    "memory_toolset",
    "read_memory",
    "validate_memory_write",
    "write_memory",
]

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


async def _load(user: User) -> str:
    return (await user_memory(user).read([user.id])).get(user.id, "")


async def _read(user: User) -> str:
    return await _load(user) or "Your memory is empty."


def edit_memory(memory: str, content: str, old_text: str | None, max_chars: int) -> str:
    """Append *content* to *memory*, or put it in place of *old_text*.

    The result may exceed *max_chars* only while it shrinks the memory, so an
    oversized memory can still be condensed.

    Raises:
        Retry: If there is nothing to append, *old_text* does not match
            exactly one passage, or the result does not fit *max_chars*.

    Examples:
        >>> edit_memory("- tea", "- green", None, 100)
        '- tea\\n- green'
        >>> edit_memory("- tea\\n- green", "- black", "- green", 100)
        '- tea\\n- black'
    """
    if old_text is None:
        if not content.strip():
            raise Retry("`content` is empty, so there is nothing to append.")

        updated = "\n".join(text for text in (memory.rstrip(), content.strip()) if text)

    else:
        try:
            replaced, _ = replace_once(memory, old_text, content)
        except ValueError as exc:
            raise Retry(
                f"`old_text` must match exactly one passage of the memory ({exc}). "
                "Copy it from the memory verbatim, with enough context to be unique."
            ) from exc

        updated = replaced.strip()

    if not memory_fits(updated, memory, max_chars):
        raise Retry(
            f"The memory would hold {len(updated)} characters, but at most "
            f"{max_chars} fit. Condense it by replacing passages with shorter ones."
        )

    return updated


@effect("read")
async def read_memory(ctx: RunContext[UserDeps]) -> str:
    """Read your persistent memory, which is preserved across conversations."""
    return await _read(ctx.deps.user)


def _change(content: str, old_text: str | None) -> partial[str]:
    return partial(
        edit_memory,
        content=content,
        old_text=old_text,
        max_chars=settings.limits.max_memory_chars,
    )


async def validate_memory_write(
    ctx: RunContext[UserDeps], content: str, old_text: str | None = None
) -> None:
    """Refuse a change that would not land, before the user is asked to approve it."""
    _ = _change(content, old_text)(await _load(ctx.deps.user))


@effect("write")
async def write_memory(
    ctx: RunContext[UserDeps],
    content: MemoryContentArg,
    old_text: MemoryOldTextArg = None,
) -> str:
    """Change your persistent memory, which is preserved across conversations.

    Without `old_text`, `content` is appended as new lines.  With it, the one
    passage matching `old_text` exactly is replaced by `content`.
    The user approves every change.
    """
    user = ctx.deps.user
    memory = user_memory(user)
    page = await memory.read([user.id])
    updated = _change(content, old_text)(page.get(user.id, ""))
    change = Update(key=user.id, value=updated, reason="The model edited its memory")

    with retry_on(Conflict):
        _ = await memory.write(Changes(items=[change]), expected=page)

    return (
        f"Memory saved, it holds {len(updated)} of "
        f"{settings.limits.max_memory_chars} characters."
    )


def memory_tools(ctx: RunContext[UserDeps]) -> list[ToolFunc | Hooked]:
    """The tools of the user's persistent memory, the same for every run."""
    return [read_memory, Hooked(write_memory, args_validator=validate_memory_write)]


memory_toolset = AgentTools(factory=memory_tools, people_only=True)
"""The memory tools, which act on one person's own data."""


async def _memory_reads(
    ctx: RunContext[UserDeps], window: Sequence[ModelMessage]
) -> list[Read]:
    """The memory as a ``read_memory`` result where the window has not read it."""
    if latest_read(window, read_memory.__name__) is not None:
        return []

    return [Read(tool=read_memory.__name__, content=await _read(ctx.deps.user))]


MEMORY_NOTE = ToolNote[UserDeps](
    reads=_memory_reads, metadata={HIDDEN_KEY: True}, id="memory-note"
)
"""Reports the memory as a ``read_memory`` call where the model has not read it.

The pair is enqueued into the first request of a conversation, or of the
window a compaction opens, and persisted with it.  So the memory the window
started with stays in the cached prefix, while a later change reaches the
model through its own ``write_memory`` call or a fresh read.  The user never
asked for it, so the transcript leaves it out (:data:`HIDDEN_KEY`).
"""
