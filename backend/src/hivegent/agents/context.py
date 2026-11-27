"""Run state the model reads as instructions that append their changes.

The date, the mode, the document selection, and the user's own instructions
change only between turns, so each is an ``on_change='append'`` instruction whose change
extends the cached prefix instead of rewriting it (see "Run state appends its
changes" in the README).
Each is an `exar.pydantic_ai.RunNote`, rendered once per run, the document
scope in a worker thread since it resolves paths on disk.
The user's instructions carry their authority, so pydantic-ai sends them
framed in the user's turn and never in the system prompt.
"""

from collections.abc import Mapping

from exar.pydantic_ai import RunNote
from pydantic_ai import RunContext

from ..prompts import (
    INTERACTIVE_MODE_NOTE,
    READ_MODE_NOTE,
    WRITE_MODE_NOTE,
    date_note,
    personal_instructions_note,
)
from ..shared.l10n import Localized
from ..types import Mode
from .common import UserDeps

__all__ = ["DATE_NOTE", "DOCUMENT_SCOPE", "MODE_NOTE", "PERSONAL_INSTRUCTIONS"]

_MODE_NOTES: Mapping[Mode, Localized[str]] = {
    "interactive": INTERACTIVE_MODE_NOTE,
    "write": WRITE_MODE_NOTE,
    "read": READ_MODE_NOTE,
}


def _date_note(ctx: RunContext[UserDeps]) -> str:
    return date_note(ctx.deps.today())[ctx.deps.language]


def _mode_note(ctx: RunContext[UserDeps]) -> str:
    return _MODE_NOTES[ctx.deps.mode][ctx.deps.language]


def _document_scope(ctx: RunContext[UserDeps]) -> str:
    return ctx.deps.describe_document_scope()


def _personal_instructions(ctx: RunContext[UserDeps]) -> str:
    instructions = ctx.deps.instructions.strip()

    if not instructions:
        return ""

    return personal_instructions_note(instructions)[ctx.deps.language]


DATE_NOTE = RunNote[UserDeps](render=_date_note, id="date")
"""Today's date, which a conversation running past midnight appends."""

MODE_NOTE = RunNote[UserDeps](render=_mode_note, id="mode")
"""What the run may change, the same tools for both modes that may write."""

DOCUMENT_SCOPE = RunNote[UserDeps](
    render=_document_scope, blocking=True, id="documents"
)
"""The documents the user selected or hid, resolved on disk off the event loop."""


PERSONAL_INSTRUCTIONS = RunNote[UserDeps](
    render=_personal_instructions, id="instructions"
).with_authority("user")
"""The user's own instructions, which a change between turns appends."""
