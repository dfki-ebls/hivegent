"""Model wrapper that compacts tool results for the LLM."""

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


def _get_formatted(part: ModelRequestPart) -> str | None:
    """Extract the compact text from a tool return part, if present."""
    if not isinstance(part, ToolReturnPart):
        return None
    # content is a ToolOutput BaseModel instance or a plain dict
    compact = getattr(part.content, "formatted", None)
    if compact is None and isinstance(part.content, dict):
        compact = part.content.get("formatted")
    return compact


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
