"""The transcripts of a turn's subagents, rendered under the call that delegated to each.

A subagent runs as a task of the `TaskControl` of pydantic-ai-harness
(``agents/subagents.py``), whose events reach the run that delegated it: the
assignment of a task (`TaskAssignedEvent`, `TaskResumedEvent`) carries the id
of the delegating call, and every event of the task's own run follows as a
`TaskProgressEvent`.  :class:`SubagentSteps` turns them into the steps of each
call, which the chat stream sends as ``data-subagent`` snapshots while the task
runs (``server/vercel/``).  The same steps, however the task ended, are what
:meth:`SubagentSteps.record` stores on the return of the call that delegated,
as the ``data-tool-output`` transcript the client renders on reload.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai.messages import (
    AgentStreamEvent,
    BaseToolReturnPart,
    FunctionToolCallEvent,
    ModelMessage,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai_harness.sub_agents import TaskProgressEvent, TaskRequestEvent

from .output import DATA_CHUNK_TYPE, ToolOutputChunk

__all__ = [
    "SUBAGENT_CHUNK_TYPE",
    "SubagentStep",
    "SubagentSteps",
    "SubagentTranscript",
    "SubagentUpdate",
    "subagent_transcript",
]

SUBAGENT_CHUNK_TYPE = "data-subagent"
"""The transient data part of a live transcript, read by ``frontend/src/lib/chat/subagent.ts``."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SubagentStep(_Frozen):
    """One coarse action of a subagent: reasoning, a message, or a tool call."""

    kind: Literal["reasoning", "message", "tool"]
    tool_name: str | None = None


class SubagentTranscript(_Frozen):
    """The steps of the subagent a call delegated to, tagged so the client recognizes it on any tool."""

    transcript: Literal["subagent"]
    steps: tuple[SubagentStep, ...]


class SubagentUpdate(_Frozen):
    """A live transcript addressed to the call that delegated to the subagent."""

    tool_call_id: str
    transcript: SubagentTranscript

    def chunk(self) -> DataChunk:
        """The transient ``data-subagent`` part the client keeps the latest of."""
        return DataChunk(
            type=SUBAGENT_CHUNK_TYPE,
            data=self.model_dump(mode="json", exclude_none=True),
            transient=True,
        )


def _step(event: AgentStreamEvent) -> SubagentStep | None:
    """The step an event of a running subagent starts, a tool call once it is complete."""
    match event:
        case PartStartEvent(part=ThinkingPart()):
            return SubagentStep(kind="reasoning")
        case PartStartEvent(part=TextPart()):
            return SubagentStep(kind="message")
        case FunctionToolCallEvent(part=part):
            return SubagentStep(kind="tool", tool_name=part.tool_name)
        case _:
            return None


def _transcript(steps: Sequence[SubagentStep]) -> SubagentTranscript:
    return SubagentTranscript(transcript="subagent", steps=tuple(steps))


@dataclass(slots=True)
class SubagentSteps:
    """The steps of a turn's subagents, by the call that delegated to each.

    Args:
        calls: The delegating call of each task by its id, the latest for a resumed one.
        steps: The steps of each call by its id.
    """

    calls: dict[str, str] = field(default_factory=dict[str, str])
    steps: dict[str, list[SubagentStep]] = field(
        default_factory=dict[str, list[SubagentStep]]
    )

    def update(self, event: AgentStreamEvent) -> SubagentUpdate | None:
        """The transcript of the call *event* extends, ``None`` for any other event."""
        match event:
            case TaskRequestEvent(task_id=task, tool_call_id=str() as call):
                self.calls[task] = call
                self.steps[call] = []

                return None

            case TaskProgressEvent(task_id=task, event=inner) if (
                call := self.calls.get(task)
            ) is not None and (step := _step(inner)) is not None:
                steps = self.steps[call]
                steps.append(step)

                return SubagentUpdate(tool_call_id=call, transcript=_transcript(steps))

            case _:
                return None

    def transcript(self, call: str) -> SubagentTranscript | None:
        """The transcript of the subagent *call* delegated to, ``None`` for another call."""
        steps = self.steps.get(call)

        return None if steps is None else _transcript(steps)

    def record(self, messages: Sequence[ModelMessage]) -> None:
        """Store the transcript of each subagent on the return of the call that delegated to it.

        It is the call's ``data-tool-output`` part, which the client renders
        like any tool's data.  Mutates in place.
        """
        for message in messages:
            for part in message.parts:
                if (
                    isinstance(part, ToolReturnPart)
                    and (transcript := self.transcript(part.tool_call_id)) is not None
                ):
                    part.metadata = ToolOutputChunk(
                        type=DATA_CHUNK_TYPE,
                        id=part.tool_call_id,
                        data=transcript.model_dump(mode="json", exclude_none=True),
                    )


def subagent_transcript(part: BaseToolReturnPart) -> SubagentTranscript | None:
    """The transcript stored on *part* (:meth:`SubagentSteps.record`), ``None`` for another return."""
    chunk = part.metadata

    if not isinstance(chunk, DataChunk) or chunk.type != DATA_CHUNK_TYPE:
        return None

    try:
        return SubagentTranscript.model_validate(chunk.data)
    except ValidationError:
        return None
