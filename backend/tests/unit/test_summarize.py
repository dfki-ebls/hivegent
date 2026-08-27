"""Tests for conversation summarization."""

from dataclasses import replace

import pytest
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from hivegent.agents import RunPrefix, UserDeps, build_capabilities, capabilities
from hivegent.agents.summarize import COMPACT_PROMPT, _plan, summarize_conversation
from hivegent.llm_config import LlmConfig
from hivegent.store import Casebase
from hivegent.types import ToolsSpec

models.ALLOW_MODEL_REQUESTS = False

_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="What does the manual say?")]),
    ModelResponse(
        parts=[
            ThinkingPart(content="the user probably wants the safety chapter"),
            ToolCallPart(
                tool_name="read_document",
                args={"file_path": "manual.md"},
                tool_call_id="1",
            ),
        ]
    ),
    ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read_document",
                content="Chapter 1 covers safety procedures.",
                tool_call_id="1",
            )
        ]
    ),
    ModelResponse(parts=[TextPart(content="It covers safety procedures.")]),
]


def _turn(text: str, *, input_tokens: int) -> list[ModelMessage]:
    """A user turn answered by a response the provider sized."""
    return [
        ModelRequest(parts=[UserPromptPart(content=text)]),
        ModelResponse(
            parts=[TextPart(content=f"re: {text}")],
            usage=RequestUsage(input_tokens=input_tokens, output_tokens=1_000),
        ),
    ]


# A conversation that grew until the next turn no longer fit: the provider
# accepted 190k, and the trailing prompt it refused is still on the list.
_MEASURED: list[ModelMessage] = [
    *_turn("first", input_tokens=100_000),
    *_turn("second", input_tokens=150_000),
    *_turn("third", input_tokens=190_000),
    ModelRequest(parts=[UserPromptPart(content="the turn that overflowed")]),
]


@pytest.fixture
def run_prefix(monkeypatch: pytest.MonkeyPatch) -> RunPrefix:
    """A run whose prefix stands in for the chat turn being compacted.

    Its model is the one thing a test swaps, which is what carrying the
    resolved model on the prefix buys: nothing has to reach for the shared
    HTTP client a real one would be built from.
    """

    async def _memory(_user_id: str) -> str:
        return "remembered"

    monkeypatch.setattr(capabilities, "load_memory", _memory)

    return RunPrefix(
        deps=UserDeps(
            user_id="u", store=Casebase(kind="user", id="u"), mode="interactive"
        ),
        capabilities=build_capabilities(ToolsSpec(), mode="interactive"),
        instructions="chat instructions",
        llm=LlmConfig(),
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[])),
    )


async def test_compaction_continues_the_conversation_instead_of_re_rendering_it(
    run_prefix: RunPrefix,
) -> None:
    """The whole point: the request is the chat prefix plus a short prompt."""
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        assert info.function_tools, "the chat turn's tools must stay declared"
        return ModelResponse(parts=[TextPart(content="  summary text  ")])

    summary = await summarize_conversation(
        _MESSAGES, replace(run_prefix, model=FunctionModel(respond))
    )

    assert summary == "summary text"
    sent = seen[0]
    # Every original message is replayed as itself, so everything ahead of the
    # appended prompt is the prefix the provider already holds.
    assert sent[: len(_MESSAGES)] == _MESSAGES
    request = sent[-1]
    assert isinstance(request, ModelRequest)
    prompt = request.parts[-1]
    assert isinstance(prompt, UserPromptPart) and prompt.content == COMPACT_PROMPT
    assert "chat instructions" in (request.instructions or "")


async def test_compaction_propagates_errors_that_are_not_overflow(
    run_prefix: RunPrefix,
) -> None:
    def reject(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=500, model_name="m", body="boom")

    with pytest.raises(ModelHTTPError):
        await summarize_conversation(
            _MEASURED, replace(run_prefix, model=FunctionModel(reject))
        )


def test_plan_offers_everything_first_then_sheds_a_reserve_at_a_time() -> None:
    """The endpoint judges the first attempt; only a refusal trims."""
    # Everything the endpoint served (191k, so all but the refused prompt),
    # then the largest prefix a reserve below that (151k), then 101k -- each
    # landing on a turn boundary rather than on a share of the list.
    assert _plan(_MEASURED) == [6, 4, 2]


async def test_compaction_sheds_again_when_the_trimmed_turn_still_overflows(
    run_prefix: RunPrefix,
) -> None:
    """A reported count the provider disagrees with costs a retry, not the run."""
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            raise ModelHTTPError(
                status_code=400,
                model_name="m",
                body={"code": "context_length_exceeded"},
            )

        return ModelResponse(parts=[TextPart(content="summary")])

    summary = await summarize_conversation(
        _MEASURED, replace(run_prefix, model=FunctionModel(respond))
    )

    assert summary == "summary"
    # The refused attempt carried every turn the endpoint had served; the retry
    # sheds from the tail, so what remains is still the block it has cached.
    assert seen[0][:-1] == _MEASURED[:6]
    assert seen[1][:-1] == _MEASURED[:4]


def test_plan_never_cuts_on_an_unanswered_tool_call() -> None:
    """A history ending on an open call is not a history a provider accepts."""
    dangling: list[ModelMessage] = [
        *_turn("first", input_tokens=100_000),
        ModelResponse(
            parts=[ToolCallPart(tool_name="read_document", args={}, tool_call_id="1")],
            usage=RequestUsage(input_tokens=150_000, output_tokens=1_000),
        ),
    ]

    assert _plan(dangling) == [2]


def test_plan_falls_back_to_everything_when_no_turn_reported_a_size() -> None:
    """Nothing to plan from means one attempt, with the endpoint as the judge."""
    assert _plan(_MESSAGES) == [len(_MESSAGES)]
