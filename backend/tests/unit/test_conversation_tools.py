"""Tests for the text the conversation tools hand the model."""

from datetime import UTC, datetime

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from hivegent.agents.tools.conversation import format_conversation
from hivegent.db.conversations import ConversationData


def test_format_conversation_renders_the_header_and_the_dialogue() -> None:
    """The model sees what was said and done, not just the title."""
    conversation = ConversationData(
        id="c1",
        title="Budget",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="What is the total?")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="grep", args={"pattern": "total"})]
            ),
            ModelRequest(
                parts=[ToolReturnPart(tool_name="grep", content="huge document")]
            ),
            ModelResponse(parts=[TextPart(content="It is 42.")]),
        ],
    )

    assert format_conversation(conversation) == (
        "c1  2026-01-02  Budget\n---\n"
        "user:\nWhat is the total?\n---\n"
        "tool call: grep(pattern='total')\n---\n"
        "assistant:\nIt is 42."
    )
