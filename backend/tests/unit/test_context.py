"""Context notes: run state reported in the history instead of the prompt."""

from collections.abc import Sequence

import pytest
from pydantic_ai import Agent, FunctionToolset, ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from hivegent.agents import context
from hivegent.agents.common import UserDeps
from hivegent.agents.context import (
    CONTEXT_TOOL,
    LIMITS,
    MEMORY,
    MODE,
    ContextNotes,
)
from hivegent.prompts import INTERACTIVE_MODE_NOTE, WRITE_MODE_NOTE
from hivegent.store import User
from hivegent.types import Mode


def _notes(messages: Sequence[ModelMessage]) -> list[str]:
    """Every ``get_context`` result in *messages*, in order."""
    return [
        part.model_response_str()
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == CONTEXT_TOOL
    ]


def _reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="ok")])


async def test_a_note_is_reported_again_only_once_it_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each turn extends the history, so an unchanged note would only repeat.

    The mode is reported again once it changes, while the memory keeps the
    state the conversation started with.
    """
    memory = ["first"]

    async def load(_user: User) -> str:
        return memory[0]

    monkeypatch.setattr(context, "load_memory", load)
    agent = Agent(deps_type=UserDeps)

    async def turn(history: list[ModelMessage], mode: Mode) -> list[ModelMessage]:
        result = await agent.run(
            "hi",
            model=FunctionModel(_reply),
            deps=UserDeps(user=User("u"), mode=mode),
            message_history=history,
            capabilities=[ContextNotes([MODE, MEMORY])],
        )

        return result.all_messages()

    history = await turn([], "interactive")
    memory[0] = "second"
    history = await turn(history, "interactive")
    history = await turn(history, "write")

    assert _notes(history) == [
        INTERACTIVE_MODE_NOTE.en,
        "first",
        WRITE_MODE_NOTE.en,
    ]


async def test_the_retry_note_reaches_the_model_and_persists() -> None:
    """Run a real agent, so the run's retry budget and run id are what decide."""
    toolset = FunctionToolset[UserDeps]()

    @toolset.tool_plain
    def broken() -> str:
        raise ModelRetry("still broken")

    warned: list[str] = []

    def call_broken(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if notes := _notes(messages[-1:]):
            warned.extend(notes)

            return ModelResponse(parts=[TextPart(content="giving up")])

        return ModelResponse(parts=[ToolCallPart(tool_name="broken", args={})])

    result = await Agent(deps_type=UserDeps, retries=4).run(
        "go",
        model=FunctionModel(call_broken),
        deps=UserDeps(user=User("u"), mode="read"),
        toolsets=[toolset],
        capabilities=[ContextNotes([LIMITS])],
    )

    assert warned == [
        (
            "`broken` failed 3 times in a row, and the turn ends after 4 retries "
            "(1 remaining). Change the arguments or the approach instead of "
            "repeating the call."
        )
    ]
    assert _notes(result.all_messages()) == warned
