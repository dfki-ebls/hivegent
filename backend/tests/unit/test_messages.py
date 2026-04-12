"""Tests for message persistence and metadata rehydration."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
from pydantic_ai.messages import ToolCallPart as PydanticToolCallPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import DataUIPart
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent.messages import load_messages, save_messages


def test_load_messages_rehydrates_data_chunks(data_dir: Any) -> None:
    """save -> load -> dump_messages produces a data-tool-output DataUIPart."""
    user_id = "testuser"
    conv_id = "conv-rt"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(
            parts=[
                PydanticToolCallPart(
                    tool_name="search",
                    tool_call_id="call_1",
                    args={"query": "test"},
                ),
            ],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="formatted",
                    tool_call_id="call_1",
                    metadata=DataChunk(type="data-tool-output", data=[{"text": "hi"}]),
                ),
            ],
        ),
    ]
    save_messages(user_id, conv_id, messages)
    loaded = load_messages(user_id, conv_id)

    ui_messages = VercelAIAdapter.dump_messages(loaded)
    all_parts = [p for msg in ui_messages for p in msg.parts]
    data_parts: list[DataUIPart] = [
        p for p in all_parts if isinstance(p, DataUIPart)
    ]
    assert len(data_parts) == 1
    assert data_parts[0].data == [{"text": "hi"}]


def test_save_messages_preserves_metadata_from_prior_turn(data_dir: Any) -> None:
    """Prior tool-return metadata survives a turn where the UI round-trip drops it."""
    user_id = "testuser"
    conv_id = "conv-preserve"

    original_metadata = DataChunk(type="data-tool-output", data=[{"text": "hi"}])
    turn_1 = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        ModelResponse(
            parts=[
                PydanticToolCallPart(
                    tool_name="search",
                    tool_call_id="call_1",
                    args={"query": "q"},
                ),
            ],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="formatted",
                    tool_call_id="call_1",
                    metadata=original_metadata,
                ),
            ],
        ),
    ]
    save_messages(user_id, conv_id, turn_1)

    # Simulate what pydantic-ai's Vercel load_messages produces on the
    # next turn: the prior tool-return comes back with metadata=None.
    turn_2 = [
        ModelRequest(parts=[UserPromptPart(content="q1")]),
        ModelResponse(
            parts=[
                PydanticToolCallPart(
                    tool_name="search",
                    tool_call_id="call_1",
                    args={"query": "q"},
                ),
            ],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="formatted",
                    tool_call_id="call_1",
                    metadata=None,
                ),
            ],
        ),
        ModelRequest(parts=[UserPromptPart(content="q2")]),
    ]
    save_messages(user_id, conv_id, turn_2)

    loaded = load_messages(user_id, conv_id)
    tool_return = loaded[2].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.metadata == original_metadata


def test_load_messages_ignores_non_data_chunk_metadata(data_dir: Any) -> None:
    """Tool metadata that isn't a DataChunk dict is left as-is."""
    user_id = "testuser"
    conv_id = "conv-non-data"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(
            parts=[
                PydanticToolCallPart(
                    tool_name="search",
                    tool_call_id="call_1",
                    args={"query": "test"},
                ),
            ],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="formatted",
                    tool_call_id="call_1",
                    metadata={"unrelated": "stuff"},
                ),
            ],
        ),
    ]
    save_messages(user_id, conv_id, messages)
    loaded = load_messages(user_id, conv_id)

    # The non-DataChunk metadata should survive unchanged
    tool_return = loaded[-1].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.metadata == {"unrelated": "stuff"}
