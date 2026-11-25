"""Tests for the summary of a conversation asked under its prefix."""

from dataclasses import replace

import pytest
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
from pydantic_ai.usage import RequestUsage, RunUsage

from hivegent.agents import RunPrefix, build_capabilities
from hivegent.agents.compaction import COMPACT_PROMPT, PrefixSummary
from hivegent.agents.guards import TOOL_OUTPUT_FENCE
from hivegent.shared.l10n import LANGUAGES, Language
from tests.helpers import MEMORY_READ, OVERFLOW, in_run

_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="What does the manual say?")]),
    *MEMORY_READ,
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


async def summarize_conversation(
    messages: list[ModelMessage], prefix: RunPrefix
) -> str:
    """The summary of *messages* asked under *prefix*, its usage counted."""
    usage = RunUsage()
    part = await in_run(
        prefix.deps, PrefixSummary(prefix=prefix).compact(messages, usage)
    )
    assert part.content is not None
    assert usage.requests >= 1

    return part.content


@pytest.mark.parametrize("language", LANGUAGES)
async def test_compaction_continues_the_conversation_instead_of_re_rendering_it(
    run_prefix: RunPrefix, language: Language
) -> None:
    """The whole point: the request is the chat prefix plus a short prompt.

    The prompt is a user turn, so it is asked in the run's own language.
    """
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        assert info.function_tools, "the chat turn's tools must stay declared"
        return ModelResponse(parts=[TextPart(content="  summary text  ")])

    summary = await summarize_conversation(
        _MESSAGES,
        replace(
            run_prefix,
            deps=replace(run_prefix.deps, language=language),
            model=FunctionModel(respond),
        ),
    )

    assert summary == "summary text"
    sent = seen[0]
    # Every original message is replayed as itself, so everything ahead of the
    # appended prompt is the prefix the provider already holds.
    assert sent[: len(_MESSAGES)] == TOOL_OUTPUT_FENCE.fence_messages(_MESSAGES)
    request = sent[-1]
    assert isinstance(request, ModelRequest)
    prompt = request.parts[-1]
    assert (
        isinstance(prompt, UserPromptPart)
        and prompt.content == COMPACT_PROMPT[language]
    )
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


async def test_compaction_sheds_again_when_the_trimmed_turn_still_overflows(
    run_prefix: RunPrefix,
) -> None:
    """A reported count the provider disagrees with costs a retry, not the run."""
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            raise OVERFLOW

        return ModelResponse(parts=[TextPart(content="summary")])

    history = [_MEASURED[0], *MEMORY_READ, *_MEASURED[1:]]
    summary = await summarize_conversation(
        history, replace(run_prefix, model=FunctionModel(respond))
    )

    assert summary == "summary"
    # The refused attempt carried every turn the endpoint had served; the retry
    # sheds from the tail, so what remains is still the block it has cached.
    assert seen[0][:-1] == TOOL_OUTPUT_FENCE.fence_messages(history[:8])
    assert seen[1][:-1] == TOOL_OUTPUT_FENCE.fence_messages(history[:6])


async def test_compaction_never_executes_a_tool_and_asks_again_without_tools(
    run_prefix: RunPrefix,
) -> None:
    """The tool schemas stay for the cache, a call is refused, and the retry withholds them."""
    offered: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append(len(info.function_tools))

        if info.function_tools:
            call = ToolCallPart(
                tool_name="write_document",
                args={"file_path": "~/notes.md", "content": "mutated"},
                tool_call_id="write-1",
            )
            return ModelResponse(parts=[call])

        return ModelResponse(parts=[TextPart(content="summary")])

    write_deps = replace(run_prefix.deps, mode="write")
    write_run = replace(
        run_prefix,
        deps=write_deps,
        capabilities=build_capabilities(write_deps),
        model=FunctionModel(respond),
    )

    assert await summarize_conversation(_MESSAGES, write_run) == "summary"
    assert offered[0] > 0 and offered[1:] == [0]
