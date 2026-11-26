"""Chat turns that run on their own, apart from the request that started them.

A turn runs as a task of :data:`runs`, one per conversation, from its
preparation to its last chunk, and keeps every chunk it streams until it ends.
However it ends, its last chunk before any error is what it *settles* on,
the stored version of the turn, since a client stops reading at an error,
unless the turn could not be stored.
The request that started it, a retry of that request, and a client
reconnecting after a reload each follow it from its first chunk, and leaving
stops only the follower.  An explicit cancel, or the server shutting down,
stops the turn itself, still preparing or running, which then stores what it
got to like a turn cut short.

A turn acts with the grants of the principal that started it, which only its
token vouches for, so it stops too once that token expired and no request of
the principal renewed it in time with a fresh one still holding them.

A turn is keyed by its :class:`OwnedConversation`, which is the owner and the
id together, so no other user finds it, even before the conversation's row
exists.  The registry
lives in the process, as the maintenance flag does, so it holds for the
single-process deployment and a turn ends with the process.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, DoneChunk, ErrorChunk

from .auth import Lease, leases, until_expiry
from .db.conversations import OwnedConversation
from .shared.l10n import Localized
from .store import User

__all__ = ["Prepared", "Run", "Runs", "Settle", "runs"]


logger = logging.getLogger(__name__)

_TURN_FAILED = Localized(
    en="The response failed. Please try again.",
    de="Die Antwort ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
)

type Settle = Callable[[], BaseChunk | None]
"""The chunk a started turn ends on once it stored what it got to, ``None`` when it could not."""


@dataclass(frozen=True, slots=True)
class Prepared:
    """A turn every check passed, ready to stream."""

    stream: AsyncIterator[BaseChunk]
    history: list[ModelMessage]
    """The history the turn runs on, the stored one or the part an edit keeps."""

    settle: Settle = lambda: None
    """What the turn settles on once it ended (:meth:`Run.pump`)."""

    after: Callable[[], Awaitable[None]] | None = None
    """Work that follows a turn that was not stopped, holding neither its slot nor its followers."""


@dataclass(slots=True, eq=False)
class Run:
    """A turn of a conversation, with every chunk it streamed so far.

    Attributes:
        message: The client message the turn answers or continues, its new
            user message or the assistant message carrying the decisions of a
            post-approval continuation, ``None`` for a regenerate or a
            compaction.
        principal: Who started the turn, with the grants it acts with.
        history: The history the turn runs on, once it is prepared.
        refusal: Why its preparation refused the turn.
        started: Whether its preparation passed, so it streams.
    """

    message: UIMessage | None
    principal: User
    history: list[ModelMessage] = field(default_factory=list[ModelMessage])
    chunks: list[BaseChunk] = field(default_factory=list[BaseChunk])
    refusal: Exception | None = None
    started: bool = False
    done: bool = False
    after: Callable[[], Awaitable[None]] | None = None
    task: asyncio.Task[None] = field(init=False)
    _changed: asyncio.Event = field(default_factory=asyncio.Event)

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _push(self, chunk: BaseChunk) -> None:
        self.chunks.append(chunk)
        self._notify()

    async def pump(self, prepare: Awaitable[Prepared]) -> None:
        """Prepare the turn, then keep every chunk it streams, waking the followers.

        The chunk the prepared turn settles on goes after everything it streamed,
        a stop included, but ahead of its errors and its end, where a client
        stops reading.
        A failure of the stream or of settling is logged and ends the turn on an
        error chunk, so no follower sees it end unexplained.
        The turn holds a lease of its principal's grants (:class:`Lease`),
        which every request of the principal renews, and stops like a
        cancelled one once it lapsed.
        """
        with leases.hold(self.principal) as lease:
            watch = asyncio.create_task(self._stop_on_lapse(lease))

            try:
                await self._pump(prepare)
            finally:
                _ = watch.cancel()

    async def _stop_on_lapse(self, lease: Lease) -> None:
        await lease.lapse()
        logger.info("Chat turn stopped, since no request renewed its authorization")
        _ = self.task.cancel()

    async def _pump(self, prepare: Awaitable[Prepared]) -> None:
        try:
            prepared = await prepare
        # Raised again by every request that follows the turn.
        except Exception as exc:  # noqa: BLE001
            self.refusal = exc
            return

        self.history = prepared.history
        self.after = prepared.after
        self.started = True
        self._notify()
        held: list[BaseChunk] = []

        def fail(exc: Exception) -> None:
            logger.error("Chat turn failed", exc_info=exc)

            if not any(isinstance(chunk, ErrorChunk) for chunk in held):
                held.append(ErrorChunk(error_text=_TURN_FAILED.current))

        try:
            async for chunk in prepared.stream:
                if isinstance(chunk, ErrorChunk | DoneChunk):
                    held.append(chunk)
                else:
                    self._push(chunk)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
        finally:
            try:
                settled = prepared.settle()
            except Exception as exc:  # noqa: BLE001
                fail(exc)
                settled = None

            if settled is not None:
                self._push(settled)

            for chunk in held:
                self._push(chunk)

    def end(self) -> None:
        """Mark the turn ended, so every follower finishes."""
        self.done = True
        self._notify()

    @property
    def error(self) -> str | None:
        """The text of the error the turn ended with, if it failed."""
        return next(
            (c.error_text for c in self.chunks if isinstance(c, ErrorChunk)), None
        )

    async def _until(self, reached: Callable[[], bool]) -> None:
        while not reached():
            _ = await self._changed.wait()

    async def prepared(self) -> bool:
        """Wait until the turn streams or ended, returning whether it streams."""
        await self._until(lambda: self.started or self.done)

        return self.started

    def follow(self, principal: User) -> AsyncGenerator[BaseChunk]:
        """Every chunk from the first, then each one as it comes, until the turn or *principal*'s token ends."""
        return until_expiry(self._chunks(), principal.expires_at)

    async def _chunks(self) -> AsyncGenerator[BaseChunk]:
        sent = 0

        while True:
            while sent < len(self.chunks):
                yield self.chunks[sent]
                sent += 1

            if self.done:
                return

            _ = await self._changed.wait()

    async def wait(self) -> None:
        """Return once the turn ended."""
        await self._until(lambda: self.done)


@dataclass(slots=True)
class Runs:
    """The turns of this process, at most one per conversation."""

    _runs: dict[OwnedConversation, Run] = field(
        default_factory=dict[OwnedConversation, Run]
    )
    _after: set[asyncio.Task[None]] = field(default_factory=set[asyncio.Task[None]])

    def get(self, conversation: OwnedConversation) -> Run | None:
        """The turn of the conversation, preparing or running, if its user holds one."""
        return self._runs.get(conversation)

    def start(
        self,
        conversation: OwnedConversation,
        principal: User,
        message: UIMessage | None,
        prepare: Awaitable[Prepared],
    ) -> Run:
        """Run the turn *prepare* yields for *principal* in the conversation's slot, which no turn may hold.

        Once started, the turn ends on the chunk it settles on (:meth:`Run.pump`).

        The slot is taken before *prepare* runs, so a second request finds it
        taken before this one wrote anything.  The task copies the request's
        context, its language among it, and leaves the slot when it ends,
        ahead of the followers, so one that saw the end finds the slot free.
        A refusal ends the task too, and :meth:`Run.prepared` tells it apart.
        The work *prepare* puts after the turn starts once it ended unstopped.
        """
        run = self._runs[conversation] = Run(message, principal)

        def finish(task: asyncio.Task[None]) -> None:
            if self._runs.get(conversation) is run:
                del self._runs[conversation]

            run.end()

            if run.after is not None and not task.cancelled():
                after = asyncio.create_task(_logged(run.after()))
                self._after.add(after)
                after.add_done_callback(self._after.discard)

        run.task = asyncio.create_task(run.pump(prepare))
        run.task.add_done_callback(finish)

        return run

    async def cancel(self, conversation: OwnedConversation) -> None:
        """Stop the turn of the conversation, if any, once it stored what it got to."""
        if (run := self.get(conversation)) is not None:
            await _stop([run.task])

    def running(self, user: User) -> set[UUID]:
        """The ids of *user*'s conversations with a turn, preparing or running."""
        return {c.id for c in self._runs if c.owner == user.id}

    async def cancel_all(self, user: User) -> None:
        """Stop every turn of *user*, once each stored what it got to."""
        await _stop([run.task for c, run in self._runs.items() if c.owner == user.id])

    async def aclose(self) -> None:
        """Stop every turn once each stored what it got to, and the work after the ended ones."""
        await _stop([run.task for run in self._runs.values()])
        await _stop(list(self._after))


async def _logged(work: Awaitable[None]) -> None:
    """Await *work*, whose failure is only logged, since the turn it follows already ended."""
    try:
        await work
    except Exception:
        logger.warning("Work after a chat turn failed", exc_info=True)


async def _stop(stopped: list[asyncio.Task[None]]) -> None:
    for task in stopped:
        _ = task.cancel()

    _ = await asyncio.gather(*stopped, return_exceptions=True)


runs = Runs()
"""The turns of this process."""
