"""Vercel AI chat adapter that emits canonical error codes.

The Vercel AI data stream protocol carries stream errors as a bare
``errorText`` string, so the frontend cannot see HTTP status codes or
finish reasons.
This module classifies errors on the server, where the exception type,
status code, and provider response body are still structured, and
prefixes context-window overflows with a stable code that the frontend
matches exactly (see ``isContextLengthError`` in
``frontend/src/lib/chat/chat-utils.ts``).

It also owns :func:`run_and_persist`, which streams a turn and mirrors the
run's message list to storage on every finish, and :func:`dump_messages_with_ids`,
which projects stored tree nodes to ``UIMessage``s whose ids are the node ids
the client addresses for edit / regenerate / branch-select.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import TypedDict

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk
from starlette.responses import Response

from ..db._common import new_id
from ..llm import is_context_overflow

__all__ = [
    "CONTEXT_LENGTH_EXCEEDED",
    "SDK_VERSION",
    "BranchInfo",
    "ChatAdapter",
    "chat_error_text",
    "dump_messages_with_ids",
    "run_and_persist",
]

logger = logging.getLogger(__name__)

CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"

# Vercel AI data-stream protocol version the frontend speaks; request parsing
# and response dumping must agree on it.
SDK_VERSION = 6

type PersistTurn = Callable[[Sequence[ModelMessage]], Awaitable[None]]


class BranchInfo(TypedDict):
    """Branch annotation merged into a forking node's ``UIMessage`` metadata.

    The camelCase keys are the wire shape the frontend reads; index and count
    are derived here from the sibling list the repository returns.
    """

    branchCount: int
    branchIndex: int
    siblingIds: list[str]


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


def dump_messages_with_ids(
    pairs: Sequence[tuple[str, ModelMessage]],
    *,
    siblings: Mapping[str, Sequence[str]] | None = None,
) -> list[UIMessage]:
    """Project ``(node_id, message)`` pairs to ``UIMessage``s keyed by node id.

    Each ``UIMessage.id`` is set to its head node's id, so the client's edit /
    regenerate / branch-select ``messageId`` maps straight back to a tree node.
    The adapter invokes the id generator with the exact objects passed here (it
    iterates this same list), so the identity lookup is an exact match and
    stays correct even when one request emits two ``UIMessage``s (system +
    user); a tool-return-only request emits none and is never looked up.  An
    unmapped object — unreachable today — degrades to a fresh non-addressable id
    rather than raising.  When given, *siblings* maps a forking node to its
    ordered sibling ids; its :class:`BranchInfo` is merged under a ``branch``
    metadata key (leaving the framework key intact) so the frontend can render
    branch navigation.
    """
    node_by_obj = {id(msg): node_id for node_id, msg in pairs}
    if siblings:
        for node_id, msg in pairs:
            sibs = siblings.get(node_id)
            if sibs is not None:
                sib_ids = list(sibs)
                branch = BranchInfo(
                    branchCount=len(sib_ids),
                    branchIndex=sib_ids.index(node_id),
                    siblingIds=sib_ids,
                )
                msg.metadata = {**(msg.metadata or {}), "branch": branch}

    def assign_id(msg: ModelMessage, _role: str, _index: int) -> str:
        return node_by_obj.get(id(msg)) or new_id()

    return ChatAdapter.dump_messages(
        [msg for _, msg in pairs],
        generate_message_id=assign_id,
        sdk_version=SDK_VERSION,
    )


async def run_and_persist[DepsT, OutputT](
    adapter: VercelAIAdapter[DepsT, OutputT],
    stream: AsyncIterator[BaseChunk],
    *,
    persist: PersistTurn,
) -> Response:
    """Stream a chat turn and persist the run's message list on every finish.

    *stream* is the still-unstarted chunk stream from ``adapter.run_stream``;
    iterating it inside ``capture_run_messages`` exposes the run's live message
    list, so whatever exists when the stream ends is persisted: the full turn on
    a clean finish, or the prompt plus completed messages on an error (the
    Vercel adapter turns run errors into an in-band error chunk, so the stream
    still ends normally).  An answer cut off mid-stream is not reconstructed:
    pydantic-ai 1.x leaves its partial out of ``capture_run_messages`` and we
    persist exactly what it holds (pydantic-ai v2 captures partials upstream, so
    the same path picks them up with no change).

    Persistence is server-authoritative with no browser echo to recover from, so
    a failed write hard-fails the turn: on a clean drain the failure surfaces as
    a trailing error chunk; on a client disconnect the write is shielded and a
    failure can only be logged.
    """

    async def relay() -> AsyncIterator[BaseChunk]:
        with capture_run_messages() as captured:
            try:
                async for chunk in stream:
                    yield chunk
            except BaseException:
                # Client disconnect / cancellation: persist what completed
                # (shielded — the client is gone, so a failure can only be
                # logged) and propagate.
                await asyncio.shield(_persist_safely(persist, list(captured)))
                raise

            try:
                await asyncio.shield(persist(list(captured)))
            except Exception:
                logger.exception("Failed to persist conversation turn")
                yield ErrorChunk(error_text="Failed to save the conversation.")

    return adapter.streaming_response(relay())
