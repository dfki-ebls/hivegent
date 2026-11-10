"""Chat turns that run on their own, apart from the request that started them.

A turn runs as a task of :data:`runs`, one per conversation, which keeps every
chunk it streams until it ends.  The request that started it, a retry of that
request, and a client reconnecting after a reload each follow it from its first
chunk, and leaving stops only the follower.  An explicit cancel, or the server
shutting down, stops the turn itself, which then stores what it got to like a
turn cut short.

The registry lives in the process, as the maintenance flag does, so it holds
for the single-process deployment and a turn ends with the process.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable
from dataclasses import dataclass, field
from uuid import UUID

from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk

__all__ = ["Run", "Runs", "runs"]


@dataclass(slots=True, eq=False)
class Run:
    """A turn of a conversation, with every chunk it streamed so far.

    Attributes:
        prompt_id: The id of the user message the turn answers, ``None`` for a
            regenerate, a compaction, or a resumed approval.
        error: The text of the error the turn ended with, if it failed.
        task: What runs the turn, ``None`` while its request still prepares it.
    """

    prompt_id: UUID | None
    chunks: list[BaseChunk] = field(default_factory=list[BaseChunk])
    error: str | None = None
    done: bool = False
    task: asyncio.Task[None] | None = None
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def pump(self, stream: AsyncIterator[BaseChunk]) -> None:
        """Keep every chunk of *stream*, waking the followers for each."""
        try:
            async for chunk in stream:
                if isinstance(chunk, ErrorChunk):
                    self.error = chunk.error_text

                async with self._changed:
                    self.chunks.append(chunk)
                    self._changed.notify_all()
        finally:
            await self.end()

    async def end(self) -> None:
        """Mark the turn ended, so every follower finishes."""
        async with self._changed:
            self.done = True
            self._changed.notify_all()

    async def follow(self) -> AsyncGenerator[BaseChunk]:
        """Every chunk from the first, then each one as it comes, until the turn ends."""
        sent = 0

        while True:
            async with self._changed:
                while sent == len(self.chunks) and not self.done:
                    _ = await self._changed.wait()

                new = self.chunks[sent:]

            if not new:
                return

            sent += len(new)

            for chunk in new:
                yield chunk

    async def wait(self) -> None:
        """Return once the turn ended."""
        async with self._changed:
            await self._changed.wait_for(lambda: self.done)


@dataclass(slots=True)
class Runs:
    """The turns of this process, at most one per conversation."""

    _runs: dict[UUID, Run] = field(default_factory=dict[UUID, Run])

    def get(self, conversation_id: UUID) -> Run | None:
        """The turn of the conversation, preparing or running, if any."""
        return self._runs.get(conversation_id)

    async def start(
        self,
        conversation_id: UUID,
        prompt_id: UUID | None,
        prepare: Awaitable[AsyncIterator[BaseChunk]],
    ) -> Run:
        """Run the turn *prepare* yields, in the conversation's slot no turn may hold.

        The slot is taken before *prepare* runs, so a second request finds it
        taken before this one wrote anything, and given up when *prepare*
        refuses the request.  The task copies the request's context, its
        language among it, and leaves the slot when the turn ends.
        """
        run = self._runs[conversation_id] = Run(prompt_id)

        try:
            stream = await prepare
        except BaseException:
            self._free(conversation_id, run)
            await run.end()
            raise

        run.task = asyncio.create_task(run.pump(stream))
        run.task.add_done_callback(lambda _: self._free(conversation_id, run))

        return run

    def _free(self, conversation_id: UUID, run: Run) -> None:
        if self._runs.get(conversation_id) is run:
            del self._runs[conversation_id]

    async def cancel(self, conversation_id: UUID) -> None:
        """Stop the turn of the conversation, if any, once it stored what it got to."""
        if (run := self._runs.get(conversation_id)) is not None:
            await _stop([run])

    async def aclose(self) -> None:
        """Stop every turn once each stored what it got to."""
        await _stop(list(self._runs.values()))


async def _stop(stopped: list[Run]) -> None:
    tasks = [run.task for run in stopped if run.task is not None]

    for task in tasks:
        _ = task.cancel()

    _ = await asyncio.gather(*tasks, return_exceptions=True)


runs = Runs()
"""The turns of this process."""
