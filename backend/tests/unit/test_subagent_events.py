"""Tests for the subagent transcript builder."""

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)

from hivegent.agents.subagent_events import SubagentStep, SubagentTranscriptBuilder


def test_builder_appends_coarse_steps() -> None:
    """Each step-starting event appends one label and snapshots the transcript.

    Reasoning/message starts and tool calls become append-only label steps; the
    returned update carries the parent id and the whole transcript so far, and
    non-starting events (e.g. text deltas) are ignored.
    """
    builder = SubagentTranscriptBuilder("parent")

    events = (
        PartStartEvent(index=0, part=ThinkingPart(content="weighing options")),
        FunctionToolCallEvent(part=ToolCallPart(tool_name="search", tool_call_id="c1")),
        PartStartEvent(index=0, part=TextPart(content="here is the answer")),
    )
    updates = [builder.on_event(event) for event in events]

    assert all(update is not None and update.tool_call_id == "parent" for update in updates)
    assert builder.transcript.steps == (
        SubagentStep(kind="reasoning"),
        SubagentStep(kind="tool", tool_name="search"),
        SubagentStep(kind="message"),
    )
