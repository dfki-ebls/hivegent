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
full message list to storage on both clean and interrupted finishes,
reconstructing the still-streaming response so an answer cut off mid-flight
is persisted too.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
)
from pydantic_ai.ui import NativeEvent
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
    events: AsyncIterator[NativeEvent],
    *,
    persist: PersistTurn,
) -> Response:
    """Stream a chat turn and mirror its full message list to storage.

    *events* is the still-unstarted native event stream from
    ``adapter.run_stream_native``; transforming it here, inside
    ``capture_run_messages``, exposes the run's live message list — the same
    one the agent appends to — so whatever exists when the stream ends is
    persisted: the whole conversation on a clean finish, or the prompt plus
    whatever streamed before an error (the Vercel adapter turns run errors into
    an in-band error chunk, so the stream still ends normally) or a client
    disconnect.

    The agent only appends a ``ModelResponse`` to that list once it has fully
    streamed, so an interrupted answer is missing from it.  We rebuild the live
    response from the part-delta events as they pass — parts re-index from zero
    per response, so an ``index == 0`` start begins a fresh one — and append its
    visible text whenever the captured tail is still a ``ModelRequest``, i.e.
    the latest response never landed.  A stopped turn then keeps the user's
    message *and* the partial answer rather than only the prompt.

    The persist runs in a shielded ``finally`` so a disconnect mid-write
    still lands the user's turn.
    """
    # TODO(pydantic-ai v2): v2's `capture_run_messages()` captures partials from
    # interrupted runs directly (v2.0.0b1), so this tap and reconstruction can be
    # deleted — revert to persisting `list(captured)`, switch `_run_chat` back to
    # `adapter.run_stream()`, and drop the explicit `state='interrupted'`.  Gated
    # on the 2.0 upgrade (still beta); see backend/README.md, "Persisting
    # interrupted chat turns".
    parts: dict[int, ModelResponsePart] = {}

    async def tapped() -> AsyncIterator[NativeEvent]:
        async for event in events:
            if isinstance(event, PartStartEvent):
                if event.index == 0:
                    parts.clear()
                parts[event.index] = event.part

            elif isinstance(event, PartDeltaEvent) and event.index in parts:
                parts[event.index] = event.delta.apply(parts[event.index])

            yield event

    async def relay() -> AsyncIterator[BaseChunk]:
        with capture_run_messages() as captured:
            try:
                async for chunk in adapter.transform_stream(tapped()):
                    yield chunk
            finally:
                messages = list(captured)
                # Keep only the streamed visible parts: a tool call interrupted
                # before it ran has no return, so persisting it would leave a
                # dangling call that breaks the next turn.
                visible = [
                    part
                    for _, part in sorted(parts.items())
                    if isinstance(part, (TextPart, ThinkingPart))
                ]
                if visible and messages and isinstance(messages[-1], ModelRequest):
                    # ``state='interrupted'`` is pydantic-ai's first-class lifecycle
                    # value for a response stopped before it finished — the same one
                    # its native ``StreamedRunResult.cancel()`` records.  It is purely
                    # informational: the agent never reads it when replaying history,
                    # so it does not affect future turns.
                    messages.append(ModelResponse(parts=visible, state="interrupted"))
                await asyncio.shield(_persist_safely(persist, messages))

    return adapter.streaming_response(relay())
