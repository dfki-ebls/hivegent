"""Run state the model reads as instructions that append their changes.

The date, the mode, the document selection, and the assistant change only
between turns, so each is an ``on_change='append'`` instruction whose change
extends the cached prefix instead of rewriting it (see "Run state appends its
changes" in the README).
Each is an `exar.pydantic_ai.RunNote`, rendered once per run, the document
scope in a worker thread since it resolves paths on disk.
The assistant's note is lowered to the authority of whoever wrote it, so
pydantic-ai sends a group's or a user's as the user's, framed in their turn,
and never in the system prompt.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from exar.pydantic_ai import RunNote
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from ..libraries import scope_author
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

__all__ = ["DATE_NOTE", "DOCUMENT_SCOPE", "MODE_NOTE", "assistant_instructions"]

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

    if assistant is None or not assistant.spec.instructions:
        return ""

    return assistant_note(
        assistant.name, scope_author(assistant.scope), assistant.spec.instructions
    )[ctx.deps.language]


DATE_NOTE = RunNote[UserDeps](render=_date_note, id="date")
"""Today's date, which a conversation running past midnight appends."""

MODE_NOTE = RunNote[UserDeps](render=_mode_note, id="mode")
"""What the run may change, the same tools for both modes that may write."""

DOCUMENT_SCOPE = RunNote[UserDeps](
    render=_document_scope, blocking=True, id="documents"
)
"""The documents the user selected or hid, resolved on disk off the event loop."""


def assistant_instructions(deps: UserDeps) -> AbstractCapability[UserDeps]:
    """The instructions of the assistant a run over *deps* acts as, at its author's authority.

    One id whatever the assistant, so pydantic-ai withdraws the earlier
    instructions where a conversation changes its assistant or drops it.
    """
    note = RunNote[UserDeps](render=_assistant_instructions, id="assistant")
    assistant = deps.assistant

    return note if assistant is None else note.with_authority(assistant.authority)
