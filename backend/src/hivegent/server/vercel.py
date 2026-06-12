"""Vercel AI chat adapter that emits canonical error codes.

The Vercel AI data stream protocol carries stream errors as a bare
``errorText`` string, so the frontend cannot see HTTP status codes or
finish reasons.
This module classifies errors on the server, where the exception type,
status code, and provider response body are still structured, and
prefixes context-window overflows with a stable code that the frontend
matches exactly (see ``isContextLengthError`` in
``frontend/src/lib/chat/chat-utils.ts``).

It also owns :func:`run_and_persist`, which streams a turn and mirrors its
full message list to storage on both clean and interrupted finishes.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk
from starlette.responses import Response

from ..llm import is_context_overflow

__all__ = [
    "CONTEXT_LENGTH_EXCEEDED",
    "ChatAdapter",
    "chat_error_text",
    "run_and_persist",
]

logger = logging.getLogger(__name__)

CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"

type PersistTurn = Callable[[Sequence[ModelMessage]], Awaitable[None]]


def chat_error_text(error: Exception) -> str:
    """Serialize *error* for the chat stream.

    Context-window overflows are prefixed with the stable
    ``context_length_exceeded`` code so the frontend can trigger
    auto-compaction with an exact match instead of fuzzy-matching
    provider prose.
    """
    if is_context_overflow(error):
        return f"{CONTEXT_LENGTH_EXCEEDED}: {error}"
    return str(error)


class ChatEventStream[DepsT, OutputT](VercelAIEventStream[DepsT, OutputT]):
    """Event stream that rewrites error chunks with canonical codes."""

    async def on_error(self, error: Exception) -> AsyncIterator[BaseChunk]:
        async for chunk in super().on_error(error):
            if isinstance(chunk, ErrorChunk):
                chunk = ErrorChunk(error_text=chat_error_text(error))
            yield chunk


class ChatAdapter[DepsT, OutputT](VercelAIAdapter[DepsT, OutputT]):
    """``VercelAIAdapter`` wired to :class:`ChatEventStream`."""

    def build_event_stream(self) -> ChatEventStream[DepsT, OutputT]:
        return ChatEventStream(
            self.run_input,
            accept=self.accept,
            sdk_version=self.sdk_version,
            server_message_id=self.server_message_id,
        )


async def _persist_safely(persist: PersistTurn, messages: Sequence[ModelMessage]) -> None:
    """Run *persist*, logging instead of raising on failure.

    It fires after the response has already streamed — and possibly during
    client-disconnect teardown — so a storage error must not crash the
    request; it is logged for follow-up instead.
    """
    try:
        await persist(messages)
    except Exception:
        logger.exception("Failed to persist conversation turn")


async def run_and_persist[DepsT, OutputT](
    adapter: VercelAIAdapter[DepsT, OutputT],
    stream: AsyncIterator[BaseChunk],
    *,
    persist: PersistTurn,
) -> Response:
    """Stream a chat turn and mirror its full message list to storage.

    *stream* is the still-unstarted event stream from ``adapter.run_stream``;
    iterating it here, inside ``capture_run_messages``, exposes the run's live
    message list — the same one the agent appends to — so whatever exists when
    the stream ends is persisted: the whole conversation on a clean finish, or
    the prompt plus whatever streamed before the failure on an error (the
    Vercel adapter turns run errors into an in-band error chunk, so the stream
    still ends normally) or a client disconnect.  A failed or stopped turn
    therefore keeps the user's message and partial output rather than
    vanishing.

    The persist runs in a shielded ``finally`` so a disconnect mid-write
    still lands the user's turn.
    """

    async def relay() -> AsyncIterator[BaseChunk]:
        with capture_run_messages() as captured:
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await asyncio.shield(_persist_safely(persist, list(captured)))

    return adapter.streaming_response(relay())
