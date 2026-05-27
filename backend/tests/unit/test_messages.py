"""Tests for message persistence and metadata rehydration."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.messages import ToolCallPart as PydanticToolCallPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import DataUIPart
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from hivegent.db.conversations import append_messages, load_messages


pytestmark = pytest.mark.asyncio


async def test_load_messages_rehydrates_data_chunks(db_initialized: None) -> None:
    """save -> load -> dump_messages produces a data-tool-output DataUIPart."""
    _ = db_initialized
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
    await append_messages(user_id, conv_id, messages)
    loaded = await load_messages(user_id, conv_id)

    ui_messages = VercelAIAdapter.dump_messages(loaded)
    all_parts = [p for msg in ui_messages for p in msg.parts]
    data_parts: list[DataUIPart] = [p for p in all_parts if isinstance(p, DataUIPart)]
    assert len(data_parts) == 1
    assert data_parts[0].data == [{"text": "hi"}]


async def test_append_messages_preserves_metadata_from_prior_turn(
    db_initialized: None,
) -> None:
    """Prior tool-return metadata survives a turn where the UI round-trip drops it."""
    _ = db_initialized
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
    await append_messages(user_id, conv_id, turn_1)

    # Simulate what pydantic-ai's Vercel load produces on the next turn:
    # the prior tool-return comes back with metadata=None.
    turn_2 = [
        *turn_1[:2],
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
    await append_messages(user_id, conv_id, turn_2)

    loaded = await load_messages(user_id, conv_id)
    tool_return = loaded[2].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.metadata == original_metadata


async def test_load_messages_ignores_non_data_chunk_metadata(
    db_initialized: None,
) -> None:
    """Tool metadata that isn't a DataChunk dict is left as-is."""
    _ = db_initialized
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
    await append_messages(user_id, conv_id, messages)
    loaded = await load_messages(user_id, conv_id)

    tool_return = loaded[-1].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.metadata == {"unrelated": "stuff"}
