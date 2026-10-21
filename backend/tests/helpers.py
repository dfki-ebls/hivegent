"""Helpers shared across the test tree.

Fixtures live in ``conftest.py``; this is for the plain functions a test
calls directly.
"""

import io

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelContextWindowExceededError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from hivegent.db.conversations import OwnedConversation
from hivegent.store import User
from hivegent.tools.base import Batch, ItemFailure
from hivegent.tools.workspace_os import ChangesetLimits

__all__ = [
    "LIMITS",
    "MEMORY_READ",
    "OVERFLOW",
    "owned",
    "png_bytes",
    "run_context",
    "single",
]

LIMITS = ChangesetLimits(max_operations=200, max_deletes=100, max_chars=20_000_000)
"""What one program may stage in a test that is not about the limits."""

OVERFLOW = ModelHTTPError.for_category(
    ModelContextWindowExceededError, status_code=400, model_name="m"
)
"""A context-window overflow as pydantic-ai raises it."""

MEMORY_READ: list[ModelMessage] = [
    ModelResponse(parts=[ToolCallPart("read_memory", {}, tool_call_id="memory")]),
    ModelRequest(
        parts=[ToolReturnPart("read_memory", "- likes tea", tool_call_id="memory")]
    ),
]
"""The read ``MemoryNote`` reports beside the first prompt of a window."""


def owned(conversation_id: str, user: User | None = None) -> OwnedConversation:
    """*conversation_id* as a conversation of *user*, ``u`` when none is given."""
    return OwnedConversation(user or User("u"), conversation_id)


def png_bytes(info: PngInfo | None = None) -> bytes:
    """A small PNG, optionally carrying the chunks sanitisation strips."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 80, 160)).save(buffer, format="PNG", pnginfo=info)

    return buffer.getvalue()


def single[R](data: Batch[R]) -> R:
    """The one item a one-item batch served, asserting it did not fail."""
    assert isinstance(data, tuple) and len(data) == 1
    item = data[0]
    assert not isinstance(item, ItemFailure)

    return item


def run_context[D](deps: D) -> RunContext[D]:
    """A run around *deps* whose model is never asked."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())
