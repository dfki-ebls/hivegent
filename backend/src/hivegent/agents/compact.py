"""Model wrapper that compacts tool results for the LLM."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai._run_context import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

__all__ = ["CompactToolResultModel"]


@dataclass(init=False)
class CompactToolResultModel(WrapperModel):
    """Replace structured tool results with compact text for LLM requests.

    Creates a shallow copy of the message list where each
    :class:`ToolReturnPart` carrying a ``formatted`` attribute (from
    :class:`~hivegent.tools.base.ToolOutput`) has its ``content``
    replaced with that compact string.  The original messages are
    never mutated, so concurrent readers (e.g. the Vercel AI stream)
    always see the full structured data.
    """

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Forward *messages* with compacted tool results."""
        return await self.wrapped.request(
            _compact_messages(messages),
            model_settings,
            model_request_parameters,
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        """Forward *messages* with compacted tool results (streaming)."""
        async with self.wrapped.request_stream(
            _compact_messages(messages),
            model_settings,
            model_request_parameters,
            run_context,
        ) as stream:
            yield stream


def _derive_text(data: object) -> str:
    """Derive model-facing text from raw data."""
    if isinstance(data, str):
        return data
    return json.dumps(data, default=str)


def _get_formatted(part: ModelRequestPart) -> str | None:
    """Extract the compact text from a :class:`ToolReturnPart`.

    Returns ``None`` when the part does not carry a ``ToolOutput``
    envelope (live instance or deserialized dict with a ``"data"`` key).
    """
    if not isinstance(part, ToolReturnPart):
        return None
    content = part.content
    # Live ToolOutput instance — eagerly resolved by the adapter, so
    # formatted is almost always set; fall back to _derive_text just
    # in case.
    from ..tools.base import ToolOutput

    if isinstance(content, ToolOutput):
        return content.text
    # Deserialized dict (loaded from storage)
    if isinstance(content, dict) and "data" in content:
        formatted = content.get("formatted")
        if isinstance(formatted, str):
            return formatted
        return _derive_text(content["data"])
    return None


def _compact_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Return a message list with tool return content replaced by compact text.

    Only creates new objects for messages that actually contain
    compactable tool returns.  All other messages are shared by
    reference.
    """
    result: list[ModelMessage] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            result.append(msg)
            continue
        parts: list[ModelRequestPart] = []
        changed = False
        for part in msg.parts:
            compact = _get_formatted(part)
            if compact is not None:
                parts.append(replace(part, content=compact))
                changed = True
            else:
                parts.append(part)
        result.append(replace(msg, parts=parts) if changed else msg)
    return result
