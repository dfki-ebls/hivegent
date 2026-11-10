"""Tests for the text the conversation tools hand the model."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from hivegent.agents.common import UserDeps
from hivegent.agents.tools import conversation as tools
from hivegent.agents.tools.conversation import format_conversation
from hivegent.db.conversations import ConversationData, OwnedConversation
from hivegent.store import User
from tests.helpers import label_id, owned, run_context


def test_format_conversation_renders_the_header_and_the_dialogue() -> None:
    """The model sees what was said and done, not just the title."""
    conversation = ConversationData(
        id=label_id("c1"),
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
        f"{label_id('c1')}  2026-01-02  Budget\n---\n"
        "user:\nWhat is the total?\n---\n"
        "tool call: grep(pattern='total')\n---\n"
        "assistant:\nIt is 42."
    )


async def test_search_conversation_searches_the_current_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an id it searches the run's own conversation, passages ranked by the store."""
    current = owned("current")

    async def found(user: User, conversation_id: UUID) -> OwnedConversation | None:
        return current if conversation_id == current.id else None

    async def search(
        conversation: OwnedConversation,
        query: str,
        render: Callable[[ModelMessage], Iterable[str]],
        *,
        limit: int,
    ) -> list[tuple[int, str]]:
        assert (conversation, query, limit) == (current, "total", 5)
        prompt = ModelRequest(parts=[UserPromptPart(content="What is the total?")])
        return [(0, text) for text in render(prompt)]

    monkeypatch.setattr(tools, "owned_conversation", found)
    monkeypatch.setattr(tools, "_search_conversation", search)
    ctx = replace(
        run_context(UserDeps(user=current.user, mode="interactive")),
        conversation_id=str(current.id),
    )

    result = await tools.search_conversation(ctx, "total")

    assert result.return_value == "[0] user:\nWhat is the total?"
