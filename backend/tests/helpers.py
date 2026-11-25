"""Helpers shared across the test tree.

Fixtures live in ``conftest.py``; this is for the plain functions a test
calls directly.
"""

import io
import json
from collections.abc import Awaitable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from exar.agents import session
from exar.tools import BatchData, ItemFailure
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic import SecretStr
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

from hivegent.agents import UserDeps
from hivegent.config import parse_uuid
from hivegent.config.llm import LlmModelSettings, LlmProviderSettings, LlmSettings
from hivegent.db.conversations import OwnedConversation
from hivegent.db.conversations.models import _conversation
from hivegent.security import OperatorUrl
from hivegent.store import User
from hivegent.tools.workspace_os import ChangesetLimits
from hivegent.types import FilterEntry

__all__ = [
    "LIMITS",
    "MEMORY_READ",
    "OVERFLOW",
    "doc",
    "folder",
    "in_run",
    "owned",
    "png_bytes",
    "run_context",
    "single",
    "sse_chunks",
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
"""The read ``MEMORY_NOTE`` reports beside the first prompt of a window."""


def doc(path: str) -> FilterEntry:
    """The filter entry of the document at *path*."""
    return FilterEntry(path=path, directory=False)


def folder(path: str) -> FilterEntry:
    """The filter entry of the directory at *path*, the empty local one being the whole store."""
    return FilterEntry(path=path, directory=True)


def sse_chunks(body: str) -> list[dict[str, Any]]:
    """The chunks of a Vercel AI stream *body*, without its closing ``[DONE]``."""
    return [
        json.loads(data)
        for event in body.split("\n\n")
        if (data := event.removeprefix("data: ").strip()) and data != "[DONE]"
    ]


def label_id(label: str) -> UUID:
    """The id a test names *label*, a UUID as a client mints it.

    >>> label_id("c1") == label_id("c1") != label_id("c2")
    True
    """
    return parse_uuid(label) or uuid5(NAMESPACE_URL, label)


async def in_run[T](deps: UserDeps, run: Awaitable[T]) -> T:
    """Awaits *run* as the root of an exar run under the config of *deps*, as a turn runs, whose policy the gate applies."""
    async with session("", deps.run_config, name="test", record=False):
        return await run


def owned(label: str, user: User | None = None) -> OwnedConversation:
    """The conversation named *label* (see :func:`label_id`) of *user*, ``u`` when none is given."""
    return _conversation((user or User("u")).id, label_id(label))


def png_bytes(info: PngInfo | None = None) -> bytes:
    """A small PNG, optionally carrying the chunks sanitisation strips."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 80, 160)).save(buffer, format="PNG", pnginfo=info)

    return buffer.getvalue()


def single[R](data: BatchData[R]) -> R:
    """The one item a one-item batch served, asserting it did not fail."""
    assert isinstance(data, tuple) and len(data) == 1
    item = data[0]
    assert not isinstance(item, ItemFailure)

    return item


def run_context[D](deps: D) -> RunContext[D]:
    """A run around *deps* whose model is never asked."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def catalog() -> LlmSettings:
    """A model catalog of one chat model beside a transcription one."""
    return LlmSettings(
        providers={
            "local": LlmProviderSettings(
                base_url=OperatorUrl("http://llmhop/v1"),
                api_key=SecretStr("operator-key"),
            )
        },
        models={
            "big": LlmModelSettings(provider="local", model="big-27b", max_tokens=4096),
            "whisper": LlmModelSettings(provider="local", model="whisper", chat=False),
        },
        default="big",
        stt="whisper",
    )
