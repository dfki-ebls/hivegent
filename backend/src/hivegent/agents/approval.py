"""What a call waits for the user with, and the note a user attaches to an approved one.

A call waits by raising :func:`ask`, whose metadata is the changeset summary a
write would make, the one detail the client renders.  Every other call the host
policy asks about (``agents/policy.py``) waits by the ``ApprovalRequired``
that ``guards.HOST_GATE`` raises before it runs, with no metadata.

A denial already reaches the model as the refusal it reads in place of a result
(``ToolDenied.message``), but an approval has no text of its own: the call just
runs.  The chat adapter therefore hands an approval's note to the call as its
``DeferredToolResults.metadata`` entry under :data:`APPROVAL_NOTE`, which
pydantic-ai exposes to the run as ``RunContext.tool_call_metadata``, and
:class:`ApprovalNotes` appends it to the tool's return.  The note is then part
of the stored result, so the model reads it on the continuation and on every
later turn that replays the history.

Every caller binds an approval to the changeset the user was shown, the chat
by the metadata its paused call stored and MCP by the summary it confirmed:
it hands the call the :func:`changes_digest` of that summary under
:data:`APPROVED_CHANGES`, and the write gate refuses a call whose changes have
since become different ones (:func:`approved_changes`), so the model asks again.

Not ``ToolReturn.content``: pydantic-ai sends that as a separate
``UserPromptPart``, which would be stored as a user turn nobody typed and
shown as one.  The note rides the return value instead, appended after
the `ToolOutputLimits` of pydantic-ai-harness bounded it, which
:meth:`ApprovalNotes.get_ordering` declares.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from exar.memory import digest
from exar.pydantic_ai.guards import with_note
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai_harness import ToolOutputLimits
from pydantic_core import to_jsonable_python

from ..tools.changes import ChangesetSummary

__all__ = [
    "APPROVAL_NOTE",
    "APPROVED_CHANGES",
    "ApprovalNotes",
    "approval_note_text",
    "approved_changes",
    "ask",
    "changes_digest",
]

APPROVAL_NOTE = "approval_note"
"""The key of the user's note in the ``DeferredToolResults.metadata`` of an approved call."""


APPROVED_CHANGES = "approved_changes"
"""The key of the :func:`changes_digest` of the changeset the user approved, in the metadata of an approved call."""


def changes_digest(summary: ChangesetSummary | Mapping[str, Any] | None) -> str:
    """The digest of the changeset *summary* a user approves, ``None`` digested too.

    The summary as a call asked with it or as its JSON came back from storage,
    whose keys may be reordered, digests alike.

    >>> changes_digest({"a": 1, "b": [2]}) == changes_digest({"b": [2], "a": 1})
    True
    """
    return digest(json.dumps(to_jsonable_python(summary), sort_keys=True))


def approved_changes(ctx: RunContext[Any]) -> str | None:
    """The digest of the changes the approval of the call of *ctx* is bound to, ``None`` for an unbound one."""
    metadata = ctx.tool_call_metadata
    digest = metadata.get(APPROVED_CHANGES) if isinstance(metadata, dict) else None

    return digest if isinstance(digest, str) else None


def ask(summary: ChangesetSummary | None = None) -> NoReturn:
    """Wait for the user's answer, showing the changeset *summary* where there is one."""
    raise ApprovalRequired(None if summary is None else to_jsonable_python(summary))


def approval_note_text(note: str) -> str:
    """Render *note* as the line appended to the approved call's result.

    >>> approval_note_text("Keep the old file.")
    'Note from the user who approved this call: Keep the old file.'
    """
    return f"Note from the user who approved this call: {note}"


@dataclass(slots=True)
class ApprovalNotes(AbstractCapability[Any]):
    """Append the user's approval note to the result of the call it approved."""

    def get_ordering(self) -> CapabilityOrdering:
        """Wrap the spill, so the note joins the bounded return and is never cut off.

        An ``after_tool_execute`` hook of an outer capability runs after the
        inner ones have, and pydantic-ai sorts the agent's capabilities and the
        run's as one list, so this holds with the spill on the agent.
        """
        return CapabilityOrdering(wraps=[ToolOutputLimits])

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
        note = metadata.get(APPROVAL_NOTE) if isinstance(metadata, dict) else None

        if not isinstance(note, str) or not note:
            return result

        return with_note(result, approval_note_text(note))
