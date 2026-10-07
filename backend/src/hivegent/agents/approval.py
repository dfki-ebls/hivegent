"""The note a user attaches to an approved tool call.

A denial already reaches the model as the refusal it reads in place of a result
(``ToolDenied.message``), but an approval has no text of its own: the call just
runs.  The chat adapter therefore hands an approval's note to the call as its
``DeferredToolResults.metadata`` entry, which pydantic-ai exposes to the run as
``RunContext.tool_call_metadata``, and :class:`ApprovalNotes` appends it to the
tool's return.  The note is then part of the stored result, so the model reads
it on the continuation and on every later turn that replays the history.

Not ``ToolReturn.content``: pydantic-ai sends that as a separate
``UserPromptPart``, which would be stored as a user turn nobody typed and
shown as one.  The note rides the return value instead, appended after
:class:`~hivegent.agents.guards.ToolOutputLimit` clamped it, which the order
of the two capabilities in :func:`~hivegent.agents.capabilities.build_capabilities`
guarantees.
"""

from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart, ToolReturn
from pydantic_ai.tools import RunContext, ToolDefinition

__all__ = ["APPROVAL_NOTE_KEY", "ApprovalNotes", "approval_note_text"]

APPROVAL_NOTE_KEY = "approval_note"
"""Key of the user's note in a call's ``DeferredToolResults.metadata`` entry."""


def approval_note_text(note: str) -> str:
    """Render *note* as the line appended to the approved call's result.

    >>> approval_note_text("Keep the old file.")
    'Note from the user who approved this call: Keep the old file.'
    """
    return f"Note from the user who approved this call: {note}"


def _with_note(value: Any, note: str) -> list[Any]:
    """Append *note* to a return value as one more item of its content list.

    A list already is one, a multimodal return's text and attachments, so the
    note joins it rather than nesting it, and anything else becomes its first
    item.  Every noted return is the same shape, and the client shows a list
    item by item.

    >>> _with_note("written", "Note.")
    ['written', 'Note.']
    >>> _with_note(["text", 1], "Note.")
    ['text', 1, 'Note.']
    """
    return [*value, note] if isinstance(value, list) else [value, note]


@dataclass(slots=True)
class ApprovalNotes(AbstractCapability[Any]):
    """Append the user's approval note to the result of the call it approved."""

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        """Add the note to the LLM-facing return, keeping the structured metadata."""
        metadata = ctx.tool_call_metadata
        note = metadata.get(APPROVAL_NOTE_KEY) if isinstance(metadata, dict) else None

        if not isinstance(note, str) or not note:
            return result

        text = approval_note_text(note)

        if isinstance(result, ToolReturn):
            return replace(result, return_value=_with_note(result.return_value, text))

        return _with_note(result, text)
