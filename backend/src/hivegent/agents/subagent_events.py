"""Structured subagent transcript: live snapshots and persisted shape.

A subagent run (the ``explore`` tool delegating to another agent) produces
reasoning, messages, and tool calls that are otherwise invisible to the
frontend, which only ever sees the tool's final return value.  These models
capture that activity as a flat, ordered list of *coarse* steps — each step
records only that the subagent reasoned, replied, or called a tool, never the
contents — and feed two transports from one pass over the run:

* live, via :class:`SubagentUpdate` — the growing transcript is streamed as a
  transient ``data-subagent`` part while the subagent runs, so the UI shows the
  timeline filling in and a long run never looks stuck.
* persisted, via :class:`SubagentTranscript` — the final transcript rides on the
  ``explore`` tool's ``ToolReturn`` metadata (a ``data-tool-output`` chunk), so
  it survives a page reload exactly like any other structured tool output.

Steps are label-only and append-only, so the live and persisted shapes are the
same model: a whole snapshot is cheap enough to resend on every new step, which
removes any need for an incremental diff protocol.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
)

__all__ = [
    "SubagentStep",
    "SubagentTranscript",
    "SubagentTranscriptBuilder",
    "SubagentUpdate",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SubagentStep(_Frozen):
    """One coarse action the subagent took: reasoning, a message, or a tool call.

    ``tool_name`` is set only when ``kind == "tool"``; reasoning and message
    steps are pure labels with no payload.
    """

    kind: Literal["reasoning", "message", "tool"]
    tool_name: str | None = None


class SubagentTranscript(_Frozen):
    """The ordered transcript of a subagent run (live snapshot and persisted shape).

    The ``transcript`` tag lets the frontend recognise any subagent tool's
    output generically, without matching on tool name.
    """

    transcript: Literal["subagent"] = "subagent"
    steps: tuple[SubagentStep, ...] = ()


class SubagentUpdate(_Frozen):
    """A live transcript snapshot addressed to its parent ``explore`` tool call."""

    tool_call_id: str
    transcript: SubagentTranscript


class SubagentTranscriptBuilder:
    """Accumulate a subagent run's native events into an ordered transcript.

    Feed each model-request-node and call-tools-node event through
    :meth:`on_event`: a step-starting event appends a label and returns a live
    :class:`SubagentUpdate` snapshot, anything else returns ``None``.  Read
    :attr:`transcript` for the persisted shape once the run finishes.
    """

    def __init__(self, tool_call_id: str) -> None:
        self._tool_call_id = tool_call_id
        self._steps: list[SubagentStep] = []

    @property
    def transcript(self) -> SubagentTranscript:
        """The accumulated transcript so far."""
        return SubagentTranscript(steps=tuple(self._steps))

    def on_event(self, event: object) -> SubagentUpdate | None:
        """Append the step *event* starts, returning the new snapshot or ``None``."""
        match event:
            case PartStartEvent(part=ThinkingPart()):
                step = SubagentStep(kind="reasoning")

            case PartStartEvent(part=TextPart()):
                step = SubagentStep(kind="message")

            case FunctionToolCallEvent(part=call):
                step = SubagentStep(kind="tool", tool_name=call.tool_name)

            case _:
                return None

        self._steps.append(step)
        return SubagentUpdate(
            tool_call_id=self._tool_call_id, transcript=self.transcript
        )
