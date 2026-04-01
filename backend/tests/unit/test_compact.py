"""Tests for the CompactToolResultModel message‐patching logic."""

from dataclasses import replace

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelMessagesTypeAdapter,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from hivegent.agents.compact import _compact_messages, _derive_text, _get_formatted
from hivegent.tools.base import ToolOutput


# -- _get_formatted -----------------------------------------------------------


class TestGetFormatted:
    """Tests for _get_formatted."""

    def test_extracts_from_tool_output_instance(self) -> None:
        part = ToolReturnPart(
            tool_name="grep",
            tool_call_id="c1",
            content=ToolOutput(data=["a"], formatted="a"),
        )
        assert _get_formatted(part) == "a"

    def test_extracts_from_dict(self) -> None:
        part = ToolReturnPart(
            tool_name="grep",
            tool_call_id="c1",
            content={"data": ["a"], "formatted": "a"},
        )
        assert _get_formatted(part) == "a"

    def test_derives_text_for_string_data(self) -> None:
        part = ToolReturnPart(
            tool_name="web_fetch",
            tool_call_id="c1",
            content=ToolOutput(data="page content"),
        )
        assert _get_formatted(part) == "page content"

    def test_derives_json_for_non_string_data(self) -> None:
        part = ToolReturnPart(
            tool_name="tool",
            tool_call_id="c1",
            content=ToolOutput(data={"key": "value"}),
        )
        assert _get_formatted(part) == '{"key": "value"}'

    def test_derives_null_for_none_data(self) -> None:
        part = ToolReturnPart(
            tool_name="get_document",
            tool_call_id="c1",
            content=ToolOutput(data=None),
        )
        assert _get_formatted(part) == "null"

    def test_derives_text_from_dict_without_formatted(self) -> None:
        part = ToolReturnPart(
            tool_name="tool",
            tool_call_id="c1",
            content={"data": "hello"},
        )
        assert _get_formatted(part) == "hello"

    def test_returns_none_for_plain_string(self) -> None:
        part = ToolReturnPart(
            tool_name="grep",
            tool_call_id="c1",
            content="plain text",
        )
        assert _get_formatted(part) is None

    def test_returns_none_for_plain_list(self) -> None:
        part = ToolReturnPart(
            tool_name="grep",
            tool_call_id="c1",
            content=["a", "b"],
        )
        assert _get_formatted(part) is None

    def test_returns_none_for_user_prompt(self) -> None:
        part = UserPromptPart(content="hello")
        assert _get_formatted(part) is None


# -- _compact_messages --------------------------------------------------------


def _make_messages(
    tool_content: object,
) -> list[ModelMessage]:
    """Build a minimal message list containing a tool return with *tool_content*."""
    return [
        ModelRequest(parts=[UserPromptPart(content="search for foo")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="grep",
                    args={"pattern": "foo"},
                    tool_call_id="c1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="grep",
                    tool_call_id="c1",
                    content=tool_content,
                )
            ]
        ),
    ]


class TestCompactMessages:
    """Tests for _compact_messages."""

    def test_replaces_tool_output_with_formatted_string(self) -> None:
        msgs = _make_messages(ToolOutput(data=["match"], formatted="result.md:1:match"))
        compacted = _compact_messages(msgs)

        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.content == "result.md:1:match"

    def test_replaces_dict_content_with_formatted_string(self) -> None:
        msgs = _make_messages({"data": ["match"], "formatted": "result.md:1:match"})
        compacted = _compact_messages(msgs)

        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.content == "result.md:1:match"

    def test_plain_content_passes_through(self) -> None:
        msgs = _make_messages("already compact")
        compacted = _compact_messages(msgs)

        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.content == "already compact"

    def test_non_request_messages_unchanged(self) -> None:
        msgs = _make_messages(ToolOutput(data=[], formatted="(none)"))
        compacted = _compact_messages(msgs)

        # ModelResponse is passed through by reference
        assert compacted[1] is msgs[1]

    def test_original_messages_not_mutated(self) -> None:
        original_content = ToolOutput(data=["match"], formatted="compact")
        msgs = _make_messages(original_content)
        _compact_messages(msgs)

        # Original part still has the ToolOutput instance
        original_part = msgs[2].parts[0]
        assert isinstance(original_part, ToolReturnPart)
        assert isinstance(original_part.content, ToolOutput)

    def test_preserves_tool_call_id(self) -> None:
        msgs = _make_messages(ToolOutput(data=[], formatted="x"))
        compacted = _compact_messages(msgs)

        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.tool_call_id == "c1"

    def test_model_response_str_after_compaction(self) -> None:
        msgs = _make_messages(ToolOutput(data=[1, 2, 3], formatted="1\n2\n3"))
        compacted = _compact_messages(msgs)

        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.model_response_str() == "1\n2\n3"

    def test_model_response_str_without_compaction(self) -> None:
        msgs = _make_messages(ToolOutput(data=[1, 2, 3], formatted="1\n2\n3"))
        tool_part = msgs[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        # Without compaction, model sees the full JSON
        raw = tool_part.model_response_str()
        assert '"data"' in raw
        assert '"formatted"' in raw

    def test_compacts_tool_output_without_formatted(self) -> None:
        msgs = _make_messages(ToolOutput(data="hello world"))
        compacted = _compact_messages(msgs)
        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.content == "hello world"

    def test_compacts_none_data_without_formatted(self) -> None:
        msgs = _make_messages(ToolOutput(data=None))
        compacted = _compact_messages(msgs)
        tool_part = compacted[2].parts[0]
        assert isinstance(tool_part, ToolReturnPart)
        assert tool_part.content == "null"


class TestCompactAfterRoundTrip:
    """Compaction works after serialization + deserialization (save/load)."""

    @pytest.fixture
    def round_tripped(self) -> list[ModelMessage]:
        msgs = _make_messages(ToolOutput(data=["a", "b"], formatted="a\nb"))
        serialized = ModelMessagesTypeAdapter.dump_json(msgs)
        return ModelMessagesTypeAdapter.validate_json(serialized)

    def test_content_is_dict_after_round_trip(
        self, round_tripped: list[ModelMessage]
    ) -> None:
        part = round_tripped[2].parts[0]
        assert isinstance(part, ToolReturnPart)
        assert isinstance(part.content, dict)

    def test_compaction_works_after_round_trip(
        self, round_tripped: list[ModelMessage]
    ) -> None:
        compacted = _compact_messages(round_tripped)
        part = compacted[2].parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.content == "a\nb"
        assert part.model_response_str() == "a\nb"

    def test_round_trip_without_formatted(self) -> None:
        msgs = _make_messages(ToolOutput(data="hello"))
        serialized = ModelMessagesTypeAdapter.dump_json(msgs)
        loaded = ModelMessagesTypeAdapter.validate_json(serialized)
        compacted = _compact_messages(loaded)
        part = compacted[2].parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.content == "hello"
