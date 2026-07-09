"""Tests for conversation summarization."""

import pytest
from pydantic_ai import BinaryContent, models
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
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

from hivegent.agents.summarize import _format_messages_for_summary, summarize_messages
from hivegent.llm import summary_model_settings
from hivegent.types import LlmConfig

models.ALLOW_MODEL_REQUESTS = False

_SETTINGS = summary_model_settings(LlmConfig())

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


def test_format_includes_everything_by_default() -> None:
    formatted = _format_messages_for_summary(_MESSAGES)
    assert "User: What does the manual say?" in formatted
    assert "Reasoning: the user probably wants the safety chapter" in formatted
    assert 'Tool call (read_document): {"file_path":"manual.md"}' in formatted
    assert (
        "Tool result (read_document): Chapter 1 covers safety procedures." in formatted
    )
    assert "Assistant: It covers safety procedures." in formatted


def test_format_toggles_drop_tool_and_reasoning_parts() -> None:
    formatted = _format_messages_for_summary(
        _MESSAGES,
        include_tool_calls=False,
        include_tool_results=False,
        include_reasoning=False,
    )
    assert "User: What does the manual say?" in formatted
    assert "Assistant: It covers safety procedures." in formatted
    assert "Reasoning" not in formatted
    assert "Tool" not in formatted


def test_format_keeps_tool_calls_while_dropping_results() -> None:
    formatted = _format_messages_for_summary(
        _MESSAGES, include_tool_results=False, include_reasoning=False
    )
    assert 'Tool call (read_document): {"file_path":"manual.md"}' in formatted
    assert "Tool result" not in formatted


def test_format_omits_binary_user_content() -> None:
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        "Look at this:",
                        BinaryContent(data=b"\x89PNG" * 100, media_type="image/png"),
                    ]
                )
            ]
        )
    ]
    assert _format_messages_for_summary(messages) == "User: Look at this:"


async def test_summarize_returns_model_output() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        part = messages[-1].parts[-1]
        assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
        assert "What does the manual say?" in part.content
        return ModelResponse(parts=[TextPart(content="  summary text  ")])

    summary = await summarize_messages(_MESSAGES, FunctionModel(respond), _SETTINGS)
    assert summary == "summary text"


async def test_summarize_propagates_model_errors() -> None:
    def reject(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=500, model_name="m", body="boom")

    with pytest.raises(ModelHTTPError):
        await summarize_messages(_MESSAGES, FunctionModel(reject), _SETTINGS)


async def test_summarize_retries_without_heavy_parts_on_overflow() -> None:
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        part = messages[-1].parts[-1]
        assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
        prompts.append(part.content)
        # The full transcript still carries the bulky tool result; reject it
        # as a context overflow so the fallback (heavy parts shed) kicks in.
        if "Chapter 1 covers safety procedures." in part.content:
            raise ModelHTTPError(
                status_code=400,
                model_name="m",
                body={"code": "context_length_exceeded"},
            )
        return ModelResponse(parts=[TextPart(content="summary")])

    summary = await summarize_messages(_MESSAGES, FunctionModel(respond), _SETTINGS)

    assert summary == "summary"
    assert len(prompts) == 2
    # The retry keeps tool calls (filenames) but drops the heavy tool result.
    assert "manual.md" in prompts[1]
    assert "Chapter 1 covers safety procedures." not in prompts[1]


async def test_summarize_retries_on_length_truncated_empty_response() -> None:
    # A near-full context leaves no room to answer: the server returns a
    # length-truncated empty response, which pydantic-ai surfaces as an
    # `UnexpectedModelBehavior`.  The shed-and-retry must still kick in.
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        part = messages[-1].parts[-1]
        assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
        prompts.append(part.content)
        if "Chapter 1 covers safety procedures." in part.content:
            raise UnexpectedModelBehavior(
                "Model token limit (provider default) exceeded before any "
                "response was generated."
            )
        return ModelResponse(parts=[TextPart(content="summary")])

    summary = await summarize_messages(_MESSAGES, FunctionModel(respond), _SETTINGS)

    assert summary == "summary"
    assert len(prompts) == 2
