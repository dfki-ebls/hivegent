"""The window a model is sent once its history holds a compaction.

A compaction is stored as a response, where a run never merges it with the
prompt around it, and sent as the request pydantic-ai renders for every model
as the framed summary (#10573).  Only an adapter that honors compaction
boundaries drops the history before it, which ``OpenAIChatModel`` does not
(upstream #7289), so :class:`CompactionWindow` does (see "Compaction is a turn
of the conversation" in ``backend/README.md``).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    post_compaction_window,
)
from pydantic_ai.models import ModelRequestContext

__all__ = ["CompactionWindow", "compaction_window"]


def compaction_window(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """The messages a model is sent for *messages*, the summary as a request.

    pydantic-ai renders it from the stored node alone, so every later turn
    sends it byte for byte.  A provider's own compaction is left to its adapter.
    """
    match window := post_compaction_window(messages):
        case [
            ModelResponse(
                parts=[CompactionPart(provider_name=None) as summary, *rest]
            ) as head,
            *tail,
        ]:
            kept = [replace(head, parts=rest)] if rest else []

            return [ModelRequest(parts=[summary]), *kept, *tail]

        case _:
            return window


@dataclass
class CompactionWindow(AbstractCapability[Any]):
    """Send each request the window from the latest compaction onward.

    Rewrites the request alone, so the stored history keeps every message.
    """

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Replace the request's messages with their compaction window."""
        request_context.messages = compaction_window(request_context.messages)

        return request_context
