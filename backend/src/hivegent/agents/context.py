"""Run state the model reads as instructions that append their changes.

The date, the mode, and the document selection change only between turns, so
each is an ``on_change='append'`` instruction whose change extends the cached
prefix instead of rewriting it (see "Run state appends its changes" in the README).
Instructions carry operator authority, so the model-written memory is a
``read_memory`` result instead (``agents/tools/memory.py``), and only the
operator's assistant joins them (``agents/tools/assistant.py``).
Each is an `exar.pydantic_ai.RunNote`, rendered once per run, the document
scope in a worker thread since it resolves paths on disk.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from exar.pydantic_ai import RunNote
from pydantic_ai import RunContext

from ..prompts import (
    INTERACTIVE_MODE_NOTE,
    READ_MODE_NOTE,
    WRITE_MODE_NOTE,
    assistant_note,
    date_note,
)
from ..shared.l10n import Localized
from ..types import Mode
from .common import UserDeps

__all__ = ["ASSISTANT_INSTRUCTIONS", "DATE_NOTE", "DOCUMENT_SCOPE", "MODE_NOTE"]

_MODE_NOTES: Mapping[Mode, Localized[str]] = {
    "interactive": INTERACTIVE_MODE_NOTE,
    "write": WRITE_MODE_NOTE,
    "read": READ_MODE_NOTE,
}


def _date_note(ctx: RunContext[UserDeps]) -> str:
    return date_note(datetime.now(UTC).date())[ctx.deps.language]


def _mode_note(ctx: RunContext[UserDeps]) -> str:
    return _MODE_NOTES[ctx.deps.mode][ctx.deps.language]


def _document_scope(ctx: RunContext[UserDeps]) -> str:
    return ctx.deps.describe_document_scope()


def _assistant_instructions(ctx: RunContext[UserDeps]) -> str:
    assistant = ctx.deps.assistant

    if assistant is None or not assistant.trusted:
        return ""

    return assistant_note(assistant.name, assistant.spec.instructions)[
        ctx.deps.language
    ]


DATE_NOTE = RunNote[UserDeps](render=_date_note, id="date")
"""Today's date, which a conversation running past midnight appends."""

MODE_NOTE = RunNote[UserDeps](render=_mode_note, id="mode")
"""What the run may change, the same tools for both modes that may write."""

DOCUMENT_SCOPE = RunNote[UserDeps](
    render=_document_scope, blocking=True, id="documents"
)
"""The documents the user selected or hid, resolved on disk off the event loop."""

ASSISTANT_INSTRUCTIONS = RunNote[UserDeps](
    render=_assistant_instructions, id="assistant-instructions"
)
"""The operator's assistant the run acts as, nothing for any other."""
