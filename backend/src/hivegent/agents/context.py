"""Run state the model reads as instructions that append their changes.

The mode and the document selection change only between turns, so each is an
``on_change='append'`` instruction whose change extends the cached prefix
instead of rewriting it (see "Run state appends its changes" in the README).
Instructions carry operator authority, so the model-written memory is a
``read_memory`` result instead (``agents/tools/memory.py``).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Self

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import InstructionPart

from ..l10n import Localized
from ..prompts import (
    INTERACTIVE_MODE_NOTE,
    READ_MODE_NOTE,
    WRITE_MODE_NOTE,
)
from ..types import Mode
from .common import UserDeps

__all__ = ["DOCUMENT_SCOPE", "MODE_NOTE", "RunNote"]

_MODE_NOTES: Mapping[Mode, Localized[str]] = {
    "interactive": INTERACTIVE_MODE_NOTE,
    "write": WRITE_MODE_NOTE,
    "read": READ_MODE_NOTE,
}


@dataclass(slots=True)
class RunNote(AbstractCapability[UserDeps]):
    """An appending instruction block rendered once per run from its deps.

    The state it renders is fixed for a run, so it is resolved once rather than
    before every request, and a literal block stays in the cacheable part of
    the instructions.  An empty rendering contributes no block.
    """

    render: Callable[[UserDeps], Awaitable[str]]
    text: str = ""
    id: str | None = field(default=None, kw_only=True)

    async def for_run(self, ctx: RunContext[UserDeps]) -> Self:
        """Render the run's state."""
        return replace(self, text=await self.render(ctx.deps))

    def get_instructions(self) -> InstructionPart | None:
        """The state as an appending block, none while it renders empty."""
        return InstructionPart(self.text, on_change="append") if self.text else None


async def _mode_note(deps: UserDeps) -> str:
    return _MODE_NOTES[deps.mode][deps.language]


async def _document_scope(deps: UserDeps) -> str:
    # Resolves the selected paths on disk.
    return await asyncio.to_thread(deps.describe_document_scope)


MODE_NOTE = RunNote(_mode_note, id="mode")
"""What the run may change, the same tools for both modes that may write."""

DOCUMENT_SCOPE = RunNote(_document_scope, id="documents")
"""The documents the user selected or hid."""
