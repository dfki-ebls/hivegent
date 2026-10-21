"""The window a model is sent once its history holds a compaction.

pydantic-ai does not yet send a provider-less ``CompactionPart`` to a Chat
Completions model, which replays the full history instead (upstream #7289).
:class:`CompactionWindow` does it in the meantime, and goes once a release
includes #10573 (see "Compaction is a turn of the conversation" in
``backend/README.md``).
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
    UserPromptPart,
    post_compaction_window,
)
from pydantic_ai.models import ModelRequestContext

__all__ = ["CompactionWindow", "compaction_window"]


def compaction_window(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """The messages a model is sent for *messages*, the summary as user text.

    Tags rather than prose frame the summary, so the rendering depends on the
    stored node alone and not on settings that may change between turns.  A
    provider's own compaction is left to its adapter.
    """
    match window := post_compaction_window(messages):
        case [
            ModelResponse(
                parts=[CompactionPart(content=str(summary), provider_name=None), *rest]
            ) as head,
            *tail,
        ]:
            text = f"<conversation_summary>\n{summary}\n</conversation_summary>"
            kept = [replace(head, parts=rest)] if rest else []

            return [ModelRequest(parts=[UserPromptPart(text)]), *kept, *tail]

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
