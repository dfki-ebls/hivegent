"""Chat turns that run on their own, apart from the request that started them.

A turn runs as a task of :data:`runs`, one per conversation, from its
preparation to its last chunk, and keeps every chunk it streams until it ends.
The request that started it, a retry of that request, and a client
reconnecting after a reload each follow it from its first chunk, and leaving
stops only the follower.  An explicit cancel, or the server shutting down,
stops the turn itself, still preparing or running, which then stores what it
got to like a turn cut short.

A turn is keyed by its :class:`OwnedConversation`, which is the owner and the
id together, so no other user finds it, even before the conversation's row
exists.  The registry
lives in the process, as the maintenance flag does, so it holds for the
single-process deployment and a turn ends with the process.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk

from .db.conversations import OwnedConversation
from .store import User

__all__ = ["Prepared", "Run", "Runs", "runs"]

type Prepared = tuple[AsyncIterator[BaseChunk], UUID | None]
"""A prepared turn, its stream and the node its branch forks from."""


@dataclass(slots=True, eq=False)
class Run:
    """A turn of a conversation, with every chunk it streamed so far.

    Attributes:
        message: The client message the turn answers or continues, its new
            user message or the assistant message carrying the decisions of a
            post-approval continuation, ``None`` for a regenerate or a
            compaction.
        fork_id: The node the turn's branch forks from, once it is prepared.
        refusal: Why its preparation refused the turn.
        started: Whether its preparation passed, so it streams.
    """

    message: UIMessage | None
    fork_id: UUID | None = None
    chunks: list[BaseChunk] = field(default_factory=list[BaseChunk])
    refusal: Exception | None = None
    started: bool = False
    done: bool = False
    task: asyncio.Task[None] = field(init=False)
    _changed: asyncio.Event = field(default_factory=asyncio.Event)

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def pump(self, prepare: Awaitable[Prepared]) -> None:
        """Prepare the turn, then keep every chunk it streams, waking the followers."""
        try:
            stream, self.fork_id = await prepare
        # Raised again by every request that follows the turn.
        except Exception as exc:  # noqa: BLE001
            self.refusal = exc
            return

        self.started = True
        self._notify()

        async for chunk in stream:
            self.chunks.append(chunk)
            self._notify()

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

    async def follow(self) -> AsyncGenerator[BaseChunk]:
        """Every chunk from the first, then each one as it comes, until the turn ends."""
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

    def get(self, conversation: OwnedConversation) -> Run | None:
        """The turn of the conversation, preparing or running, if its user holds one."""
        return self._runs.get(conversation)

    def start(
        self,
        conversation: OwnedConversation,
        message: UIMessage | None,
        prepare: Awaitable[Prepared],
    ) -> Run:
        """Run the turn *prepare* yields in the conversation's slot, which no turn may hold.

        The slot is taken before *prepare* runs, so a second request finds it
        taken before this one wrote anything.  The task copies the request's
        context, its language among it, and leaves the slot when it ends,
        ahead of the followers, so one that saw the end finds the slot free.
        A refusal ends the task too, and :meth:`Run.prepared` tells it apart.
        """
        run = self._runs[conversation] = Run(message)

        def finish(_: asyncio.Task[None]) -> None:
            if self._runs.get(conversation) is run:
                del self._runs[conversation]

            run.end()

        run.task = asyncio.create_task(run.pump(prepare))
        run.task.add_done_callback(finish)

        return run

    async def cancel(self, conversation: OwnedConversation) -> None:
        """Stop the turn of the conversation, if any, once it stored what it got to."""
        if (run := self.get(conversation)) is not None:
            await _stop([run])

    async def cancel_all(self, user: User) -> None:
        """Stop every turn of *user*, once each stored what it got to."""
        await _stop([run for c, run in self._runs.items() if c.owner == user.id])

    async def aclose(self) -> None:
        """Stop every turn once each stored what it got to."""
        await _stop(list(self._runs.values()))


async def _stop(stopped: list[Run]) -> None:
    for run in stopped:
        _ = run.task.cancel()

    _ = await asyncio.gather(*(run.task for run in stopped), return_exceptions=True)


runs = Runs()
"""The turns of this process."""
