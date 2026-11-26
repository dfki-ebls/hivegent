"""The delegated tasks of each conversation, which outlive the turn that started them.

Every chat turn of a conversation binds the conversation's one task owner, a
`DelegationTasks` of pydantic-ai-harness (:meth:`Delegations.turn`), so the
tasks its main agent delegates run in the background past the turn, and a
later turn checks on, steers, cancels, or resumes them.  The owner saves each
task with its history in the conversation's ``tasks`` folder beside its
``/tmp`` (:data:`TASKS_FOLDER`), so the tasks list after a restart, where one
that was running loads as cancelled, and they go with the conversation.

The owner stays open while a turn binds it, a task runs, a report waits, or
usage waits to be stored, holding a lease of the grants of the user who
opened it (:class:`~hivegent.auth.Lease`), and closes once none holds or the
lease lapsed, which cancels the tasks still running, by cancelling the task
that keeps it open, as closing a conversation does.  A report reaches a
running turn at its next model request, and one that no turn takes wakes the
conversation (:data:`Wake`), with a turn that reads the reports as its
first request.

Tasks share the usage of the turn that started them and inherit its limits,
so that turn's budget bounds them however long they run, and what they spend
after it was stored is stored by the next turn (:meth:`Delegations.usage_for_save`).

The registry lives in the process like :data:`hivegent.runs.runs`, so it holds
for the single-process deployment and the tasks end with the process.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Generator, Iterable
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    PartStartEvent,
)
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.sub_agents import (
    DelegationTask,
    DelegationTaskEvent,
    DelegationTasks,
)

from .auth import Lease, leases
from .config import settings
from .db.conversations import OwnedConversation
from .jobs import ConversationActivity, manager
from .shared.tasks import cancel_and_drain
from .store import User

__all__ = ["TASKS_FOLDER", "Delegations", "Wake", "delegations"]

logger = logging.getLogger(__name__)

TASKS_FOLDER = "tasks"
"""The folder below a conversation's own where its task owner saves the tasks."""

type Wake = Callable[[OwnedConversation, User], None]
"""Starts a turn without a prompt in a conversation as a user, once no turn runs, to read the reports waiting."""


@dataclass(slots=True, eq=False)
class _Spent:
    """The usage of a turn its tasks may still spend into, and how much of it the conversation stored."""

    usage: RunUsage
    stored: RunUsage


@dataclass(slots=True, eq=False)
class _Entry:
    """The open task owner of a conversation and what keeps it open."""

    conversation: OwnedConversation
    owner: DelegationTasks
    wake: Wake
    lease: Lease | None = None
    keeper: asyncio.Task[None] | None = None
    turns: int = 0
    spent: list[_Spent] = field(default_factory=list[_Spent])
    opened: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def reported(self) -> bool:
        """Whether reports of the tasks wait for a turn to read them."""
        return bool(self.owner.reports(conversation_id=self.conversation.key))

    @property
    def busy(self) -> bool:
        """Whether a turn binds the owner, a task runs, a report waits, or usage waits to be stored."""
        return (
            self.turns > 0
            or self.owner.running_count(self.conversation.key) > 0
            or self.reported
            or any(spent.usage != spent.stored for spent in self.spent)
        )


def _shown(event: DelegationTaskEvent) -> bool:
    """Whether *event* changes what a client shows of the task: its state, or a step of its run starting or ending."""
    return event.event is None or isinstance(
        event.event, PartStartEvent | FunctionToolResultEvent
    )


@dataclass(slots=True)
class Delegations:
    """The task owners of this process, at most one open per conversation."""

    _entries: dict[OwnedConversation, _Entry] = field(
        default_factory=dict[OwnedConversation, _Entry]
    )
    _keepers: dict[OwnedConversation, asyncio.Task[None]] = field(
        default_factory=dict[OwnedConversation, asyncio.Task[None]]
    )
    _suspended: Counter[OwnedConversation | str | None] = field(
        default_factory=Counter[OwnedConversation | str | None]
    )

    @asynccontextmanager
    async def turn(
        self, conversation: OwnedConversation, principal: User, wake: Wake
    ) -> AsyncGenerator[DelegationTasks]:
        """The conversation's task owner, open and kept open while the turn of *principal* runs.

        Bind it around the turn's run (``DelegationTasks.bind``).  Once the
        turn ended, a report it did not take wakes the conversation with *wake*.

        Raises:
            RuntimeError: When the owner could not open, or closed as it did.
        """
        entry = self._enter(conversation, principal, wake)

        try:
            await entry.opened.wait()

            if not self._live(entry):
                raise RuntimeError("The delegated tasks of the conversation closed")

            yield entry.owner
        finally:
            entry.turns -= 1
            self._check(entry)

    def usage_for_save(
        self, conversation: OwnedConversation, usage: RunUsage
    ) -> tuple[RunUsage, Callable[[], None]]:
        """The turn's *usage* with what tasks spent into earlier turns since those were stored, and what records that a turn stored it.

        Both take the usage as it is now, so what tasks spend while the turn
        is stored goes to the next.
        """
        entry = self._entries.get(conversation)
        current = deepcopy(usage)

        if entry is None:
            return current, lambda: None

        earlier = [(s, deepcopy(s.usage)) for s in entry.spent if s.usage is not usage]
        # RunUsage arithmetic copies shallowly, sharing and changing the details of its left side.
        total = sum((deepcopy(now) - s.stored for s, now in earlier), deepcopy(current))

        def stored() -> None:
            for spent, now in earlier:
                spent.stored = now

            entry.spent = [*(spent for spent, _ in earlier), _Spent(usage, current)]
            self._check(entry)

        return total, stored

    def pending(self, conversation: OwnedConversation) -> bool:
        """Whether reports of the conversation's tasks wait for a turn to read them, which no deletion suspends."""
        entry = self._entries.get(conversation)
        scopes = {None, conversation.owner, conversation}

        return entry is not None and entry.reported and not scopes & self._suspended.keys()

    @contextmanager
    def suspend_wakeups(
        self, scope: OwnedConversation | str | None = None
    ) -> Generator[None]:
        """Wake no conversation of *scope*, one or those of an owner id or all, while it is deleted."""
        self._suspended[scope] += 1

        try:
            yield
        finally:
            # Subtracting drops the counts that reach zero.
            self._suspended -= Counter([scope])

    async def tasks(self, conversation: OwnedConversation) -> list[DelegationTask]:
        """The conversation's tasks, live while its owner is open, else as saved, without their histories."""
        if (entry := self._open_entry(conversation)) is not None:
            return list(entry.owner.records.values())

        saved = await DelegationTasks.discover(self._folder(conversation))

        # Without an owner in this process, a task saved as running was interrupted, as opening one would settle it.
        return [
            replace(task, status="finished", outcome="cancelled")
            if task.status == "running"
            else task
            for task in saved.tasks.values()
        ]

    async def history(
        self, conversation: OwnedConversation, task_id: str
    ) -> list[ModelMessage] | None:
        """The history of the conversation's task *task_id*, ``None`` for no such task."""
        if (entry := self._open_entry(conversation)) is not None:
            if task_id not in entry.owner.records:
                return None

            return await asyncio.to_thread(entry.owner.history, task_id)

        saved = await DelegationTasks.discover(self._folder(conversation))
        task = saved.tasks.get(task_id)

        return None if task is None else task.messages

    async def cancel(self, conversation: OwnedConversation, task_id: str) -> None:
        """Stop the conversation's running task *task_id* as its user, which the model then cannot resume."""
        entry = self._open_entry(conversation)

        if (
            entry is not None
            and (task := entry.owner.records.get(task_id)) is not None
            and task.status == "running"
        ):
            await entry.owner.cancel(task_id)

    async def close(self, conversation: OwnedConversation) -> None:
        """Stop the conversation's tasks and close its owner, once it saved them."""
        await self._stop(c for c in self._keepers if c == conversation)

    async def close_all(self, user: User) -> None:
        """Stop the tasks of every conversation of *user*."""
        await self._stop(c for c in self._keepers if c.owner == user.id)

    async def aclose(self) -> None:
        """Stop every task and close every owner."""
        await self._stop(list(self._keepers))

    def _folder(self, conversation: OwnedConversation) -> Path:
        return conversation.folder(settings.data_dir) / TASKS_FOLDER

    def _live(self, entry: _Entry) -> bool:
        """Whether *entry* is the conversation's, which it stops being once its owner closes."""
        return self._entries.get(entry.conversation) is entry

    def _open_entry(self, conversation: OwnedConversation) -> _Entry | None:
        entry = self._entries.get(conversation)

        return entry if entry is not None and entry.opened.is_set() else None

    def _enter(
        self, conversation: OwnedConversation, principal: User, wake: Wake
    ) -> _Entry:
        """The conversation's entry with one more turn, opening an owner once the last one closed."""
        entry = self._entries.get(conversation)

        if entry is None:
            entry = self._entries[conversation] = _Entry(
                conversation,
                DelegationTasks(directory=self._folder(conversation)),
                wake,
            )

            async def observe(event: DelegationTaskEvent) -> None:
                self._observe(entry, event)

            entry.owner.observer = observe
            entry.keeper = self._keepers[conversation] = asyncio.create_task(
                self._keep(entry, principal, self._keepers.get(conversation))
            )

        entry.wake = wake
        entry.turns += 1

        return entry

    async def _keep(
        self, entry: _Entry, principal: User, previous: asyncio.Task[None] | None
    ) -> None:
        """Hold the entry's owner open under a lease of *principal* until it is closed or the lease lapsed.

        It opens once the *previous* owner of the conversation closed, which
        saves into the same folder, and :meth:`_check` closes it once idle.
        """
        try:
            if previous is not None:
                _ = await asyncio.gather(previous, return_exceptions=True)

            with leases.hold(principal) as lease:
                entry.lease = lease

                async with entry.owner.opened():
                    entry.opened.set()

                    try:
                        await lease.lapse()
                    finally:
                        # Left before the owner drains, so a turn starting meanwhile opens the next one.
                        self._leave(entry)
        except Exception:
            logger.exception("The delegated tasks of a conversation failed")
        finally:
            self._leave(entry)
            entry.opened.set()

            if self._keepers.get(entry.conversation) is asyncio.current_task():
                del self._keepers[entry.conversation]

    def _leave(self, entry: _Entry) -> None:
        if self._live(entry):
            del self._entries[entry.conversation]

    def _observe(self, entry: _Entry, event: DelegationTaskEvent) -> None:
        """Tell the conversation's clients what changed, and wake it or let it close once a task settled."""
        if _shown(event):
            manager.publish(
                entry.conversation.owner,
                ConversationActivity(
                    conversation_id=entry.conversation.id, kind="tasks"
                ),
            )

        if event.event is None and event.task.status != "running":
            self._check(entry)

    def _check(self, entry: _Entry) -> None:
        """Wake the conversation for the reports no turn takes, or close the owner once idle."""
        if not self._live(entry) or entry.lease is None or entry.keeper is None:
            return

        if entry.turns == 0 and self.pending(entry.conversation):
            entry.wake(entry.conversation, entry.lease.current)
        elif not entry.busy:
            self._leave(entry)
            _ = entry.keeper.cancel()

    async def _stop(self, conversations: Iterable[OwnedConversation]) -> None:
        keepers = [self._keepers[c] for c in list(conversations) if c in self._keepers]
        _ = await asyncio.gather(*(cancel_and_drain(keeper) for keeper in keepers))


delegations = Delegations()
"""The task owners of this process."""
