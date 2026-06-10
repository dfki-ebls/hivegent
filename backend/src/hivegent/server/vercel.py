"""Vercel AI chat adapter that emits canonical error codes.

The Vercel AI data stream protocol carries stream errors as a bare
``errorText`` string, so the frontend cannot see HTTP status codes or
finish reasons.
This module classifies errors on the server, where the exception type,
status code, and provider response body are still structured, and
prefixes context-window overflows with a stable code that the frontend
matches exactly (see ``isContextLengthError`` in
``frontend/src/lib/chat/chat-utils.ts``).
"""

from collections.abc import AsyncIterator, Mapping

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk

__all__ = ["CONTEXT_LENGTH_EXCEEDED", "ChatAdapter", "chat_error_text"]

CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"


def chat_error_text(error: Exception) -> str:
    """Serialize *error* for the chat stream.

    Context-window overflows are prefixed with the stable
    ``context_length_exceeded`` code so the frontend can trigger
    auto-compaction with an exact match instead of fuzzy-matching
    provider prose.
    """
    if _is_context_overflow(error):
        return f"{CONTEXT_LENGTH_EXCEEDED}: {error}"
    return str(error)


def _is_context_overflow(error: Exception) -> bool:
    """Whether *error* means the conversation overflowed the context window.

    Classifies on the structure the exception offers first: OpenAI rejects
    an oversized prompt with a 400 whose body carries ``code ==
    "context_length_exceeded"``. Message matching remains only where no
    structure exists: vLLM's 400 body is plain prose, and pydantic-ai
    collapses an empty response with ``finish_reason == "length"`` into
    the message of an ``UnexpectedModelBehavior``.
    """
    match error:
        case ModelHTTPError(status_code=400, body=body):
            if isinstance(body, Mapping) and body.get("code") == CONTEXT_LENGTH_EXCEEDED:
                return True
            return "maximum context length" in str(body)
        case UnexpectedModelBehavior(message=message):
            return "exceeded before any response was generated" in message
        case _:
            return False


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
