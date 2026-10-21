"""Tests for compaction in place and the window it opens."""

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from hivegent.agents import RunPrefix, user_agent
from hivegent.agents.compaction import (
    Compacted,
    CompactOnOverflow,
    _boundaries,
    compaction_cut,
    is_compaction,
)
from hivegent.agents.summarize import COMPACT_PROMPT
from hivegent.agents.window import compaction_window
from hivegent.db.conversations import is_user_request
from tests.helpers import MEMORY_READ, OVERFLOW


def _prompt(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _tool_round(call_id: str) -> list[ModelMessage]:
    """A tool call and its return."""
    return [
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="read_document", args={}, tool_call_id=call_id)
            ],
            model_name="m",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_document", content="more", tool_call_id=call_id
                )
            ]
        ),
    ]


_TURN = [
    _prompt("What does the manual say?"),
    *_tool_round("1"),
    ModelResponse(
        parts=[TextPart(content="It covers safety procedures.")], model_name="m"
    ),
]
_COMPACTION = ModelResponse(parts=[CompactionPart(content="the gist")])


@pytest.mark.parametrize(
    ("messages", "expected", "cut"),
    [
        # After the answered tool round and after the answer, never on a prompt,
        # so a trailing prompt follows the summary.
        ([*_TURN, _prompt("And the appendix?")], [3, 4], 4),
        # Never between a call and its return.
        ([*_TURN[:3], *_tool_round("2")], [3, 5], 5),
        # Only with something new past the latest compaction.
        ([*_TURN, _COMPACTION, _prompt("go on")], [], None),
        ([*_TURN, _COMPACTION, *_TURN], [8, 9], 9),
        # Never ahead of a call still awaiting its answer.
        (_TURN[:2], [], None),
        # Never past a prompt before a model answered it.
        ([*_TURN, _prompt("go on"), *MEMORY_READ], [3, 4], None),
    ],
)
def test_boundaries(
    messages: list[ModelMessage], expected: list[int], cut: int | None
) -> None:
    assert _boundaries(messages) == expected
    assert compaction_cut(messages) == cut


def test_window_renders_the_summary_and_drops_what_it_replaced() -> None:
    prompt = _prompt("go on")

    window = compaction_window([*_TURN, _COMPACTION, prompt])

    assert len(window) == 2
    assert window[1] is prompt
    summary = window[0].parts[0]
    assert isinstance(summary, UserPromptPart)
    assert (
        summary.content == "<conversation_summary>\nthe gist\n</conversation_summary>"
    )
    assert compaction_window(_TURN) == _TURN


_HISTORY = [_TURN[0], *MEMORY_READ, *_TURN[1:]]
_SUMMARY = "<conversation_summary>\n{}\n</conversation_summary>"


class _Overflowing:
    """A model refusing its *refused* request, after a tool round before it."""

    def __init__(self, run_prefix: RunPrefix, refused: int = 1) -> None:
        self.refused = refused
        self.seen: list[list[ModelMessage]] = []
        model = FunctionModel(self._respond, stream_function=self._stream)
        self.run = replace(run_prefix, model=model)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(messages)

        if len(self.seen) == self.refused:
            raise OVERFLOW

        if len(self.seen) < self.refused:
            call = ToolCallPart(tool_name="read_document", args={}, tool_call_id="d")
            return ModelResponse(parts=[call])

        return ModelResponse(parts=[TextPart(content=f"reply {len(self.seen)}")])

    async def _stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        response = self._respond(messages, info)

        for call in response.tool_calls:
            yield {
                0: DeltaToolCall(call.tool_name, "{}", tool_call_id=call.tool_call_id)
            }

        if response.text:
            yield response.text

    async def run_turn(
        self, prompt: str | None, history: list[ModelMessage]
    ) -> tuple[list[ModelMessage], list[Compacted]]:

        with capture_run_messages() as captured:
            async with user_agent.run_stream_events(
                prompt,
                message_history=list(history),
                deps=self.run.deps,
                model=self.run.model,
                instructions=self.run.instructions,
                capabilities=[*self.run.capabilities, CompactOnOverflow(self.run)],
            ) as events:
                compacted = [
                    event async for event in events if isinstance(event, Compacted)
                ]

        return captured, compacted


def _prompts(message: ModelMessage) -> list[object]:
    return [part.content for part in message.parts if isinstance(part, UserPromptPart)]


def _compaction_index(messages: list[ModelMessage]) -> int:
    return next(i for i, message in enumerate(messages) if is_compaction(message))


async def test_an_overflowing_prompt_is_compacted_ahead_of_it_and_retried(
    run_prefix: RunPrefix,
) -> None:
    """The summary reuses the cached prefix, and the retry sends only its window."""
    model = _Overflowing(run_prefix)
    captured, compacted = await model.run_turn("next question", _HISTORY)

    # The refused turn, the summary asked as its next turn, then the retry.
    assert model.seen[1][: len(_HISTORY)] == _HISTORY
    # Summary and prompt merged into the one request the retry sends.
    assert _prompts(model.seen[2][0]) == [_SUMMARY.format("reply 2"), "next question"]
    assert [event.part for event in compacted] == [CompactionPart(content="reply 2")]
    # Stored ahead of the prompt it was made for, past the persisted prefix.
    assert captured[: len(_HISTORY)] == _HISTORY
    assert _compaction_index(captured) == len(_HISTORY)
    assert is_user_request(captured[len(_HISTORY) + 1])
    # The window reports the memory again.
    assert [
        part.tool_name
        for part in captured[len(_HISTORY) + 2].parts
        if isinstance(part, ToolCallPart)
    ] == ["read_memory"]


async def test_a_refused_tool_return_is_summarized_and_the_run_continues(
    run_prefix: RunPrefix,
) -> None:
    """The compaction follows what the client saw, live and once stored."""
    model = _Overflowing(run_prefix, refused=2)
    captured, _ = await model.run_turn("next question", _HISTORY)

    index = _compaction_index(captured)
    # Right after the refused request that closed the tool round.
    round_call, refused = captured[index - 2 : index]
    assert isinstance(round_call, ModelResponse) and round_call.tool_calls
    assert isinstance(refused, ModelRequest)
    assert _prompts(model.seen[2][-1]) == [COMPACT_PROMPT["en"]]
    assert _prompts(model.seen[3][0]) == [_SUMMARY.format("reply 3")]


async def test_a_regenerated_prompt_is_compacted_ahead_of_it(
    run_prefix: RunPrefix,
) -> None:
    """The prompt is persisted already, so the compaction takes its place."""
    model = _Overflowing(run_prefix)
    captured, _ = await model.run_turn(None, [*_HISTORY, _prompt("next question")])

    assert _compaction_index(captured) == len(_HISTORY)
    assert is_user_request(captured[len(_HISTORY) + 1])
