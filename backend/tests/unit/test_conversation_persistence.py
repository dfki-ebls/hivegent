"""Tests for selecting which messages a finished turn persists.

Regression coverage for duplicated messages: the Vercel SDK echoes the
whole conversation back each turn and re-segments it, so a turn's new
messages must be located from the run's own ``new_messages()`` boundary,
never by diffing against the stored row count.
"""

from collections.abc import Sequence

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

from hivegent.db.conversations import messages_to_persist


def test_fresh_turn_keeps_prompt_and_generated() -> None:
    """A new question persists its folded user prompt plus everything generated."""
    prior = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        ModelResponse(parts=[TextPart(content="a1")]),
    ]
    prompt = ModelRequest(parts=[UserPromptPart(content="q2")])
    generated = [ModelResponse(parts=[TextPart(content="a2")])]

    all_messages = [*prior, prompt, *generated]
    assert messages_to_persist(all_messages, generated) == [prompt, *generated]


def test_approval_resume_skips_already_stored_turn() -> None:
    """Resuming an approved tool persists only the generated continuation.

    The assistant message that requested approval was stored when the run
    first paused; on resume it sits just before the generated tail, so it
    must not be appended again.
    """
    stored_tool_call = ModelResponse(
        parts=[ToolCallPart(tool_name="write", args={}, tool_call_id="w1")]
    )
    generated = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="write", content="ok", tool_call_id="w1")]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    all_messages = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        stored_tool_call,
        *generated,
    ]
    assert messages_to_persist(all_messages, generated) == generated


async def _run_turn(ui_messages: list[UIMessage]) -> AgentRunResult[str]:
    agent = Agent(model=TestModel(custom_output_text="ANSWER"), output_type=str)

    @agent.tool
    def lookup(ctx: RunContext[None], query: str) -> str:
        return "DOC-CONTENT"

    adapter = VercelAIAdapter(
        agent=agent,
        run_input=SubmitMessage(
            id="c1", messages=ui_messages, trigger="submit-message"
        ),
    )
    captured: list[AgentRunResult[str]] = []

    async def on_complete(result: AgentRunResult[str]) -> None:
        captured.append(result)

    async for _ in adapter.run_stream(on_complete=on_complete):
        pass
    return captured[0]


def _texts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, (UserPromptPart, TextPart))
        and isinstance(part.content, str)
    ]


async def test_persisted_turn_excludes_echoed_history() -> None:
    """A second turn persists only its own prompt and reply, not the echo.

    Mirrors the frontend: the first turn is dumped to UI messages and sent
    back with a new prompt appended, exactly the round-trip that used to
    re-append the previous turn's tail.
    """
    first = await _run_turn(
        [UIMessage(id="m1", role="user", parts=[TextUIPart(text="q1")])]
    )
    echoed = list(VercelAIAdapter.dump_messages(first.all_messages()))
    echoed.append(UIMessage(id="m2", role="user", parts=[TextUIPart(text="q2")]))

    second = await _run_turn(echoed)
    persisted = messages_to_persist(second.all_messages(), second.new_messages())

    assert isinstance(persisted[0].parts[0], UserPromptPart)
    assert "q2" in _texts(persisted)
    assert "q1" not in _texts(persisted)
    assert "a1" not in _texts(persisted)
