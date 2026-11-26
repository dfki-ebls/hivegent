"""Generic in-process background-job framework.

A reusable layer for running asynchronous work off the request path with
live status, bounded concurrency, cooperative cancellation, and a
per-owner event feed.  A feature submits a coroutine plus metadata; the
manager runs it as an :class:`asyncio.Task`, tracks its lifecycle, and
broadcasts every state change to subscribers (the SSE feed consumes
these).

The feed also carries :class:`~hivegent.tools.changes.WorkspaceChanged`,
:class:`ConversationsChanged`, :class:`LibraryChanged`, and :class:`SourceChanged`, so a client
learns that its state changed through the one channel it already watches
instead of a second push mechanism invented per feature.  :meth:`JobManager.publish` reaches every subscription
of one owner, :meth:`JobManager.broadcast` every subscription, and a
subscription also follows opaque topics, such as the workspaces its owner may
read, for which :meth:`JobManager.notify` offers each one the event its topics
let it see, whoever caused the change.

The registry is in-memory only: jobs do not survive a process restart.
Each feature keeps its own durable state consistent when a job is
cancelled or fails, e.g. the document pipeline rolls its workspace and
SQL writes back.  The framework itself knows nothing about documents; it
carries only generic, JSON-serialisable status fields so any subsystem
can reuse it.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from .auth import leases
from .config import settings
from .shared.l10n import Localized
from .store import User
from .tools.changes import WorkspaceChanged

__all__ = [
    "ADMINS",
    "ConversationActivity",
    "ConversationsChanged",
    "FeedEvent",
    "FeedReady",
    "JobContext",
    "JobManager",
    "JobProgress",
    "JobStatus",
    "JobView",
    "JobWork",
    "LibraryChanged",
    "Reservation",
    "SourceChanged",
    "manager",
]

logger = logging.getLogger(__name__)

ADMINS = "role:admin"
"""The owner of what every admin follows, such as a source's sync, which no user id can name."""

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

_JOB_FAILED = Localized(en="Job failed", de="Auftrag fehlgeschlagen")
_AUTHORIZATION_EXPIRED = Localized(
    en="The sign-in that queued this job expired before it started, please try again",
    de="Die Anmeldung, die diesen Auftrag eingereiht hat, lief vor seinem Start ab, bitte erneut versuchen",
)


class JobProgress(BaseModel):
    """Discrete progress of a job, e.g. files processed in a collection."""

    current: int
    total: int


def _percent(progress: JobProgress) -> int:
    """Integer completion percent, used to coalesce high-frequency updates."""
    if progress.total <= 0:
        return 100
    return progress.current * 100 // progress.total


def _error_message(exc: BaseException) -> str:
    """Human-readable message for a failed job, without coupling to a framework.

    Prefers a ``detail`` attribute (so a FastAPI ``HTTPException`` surfaces its
    message instead of the ``"<status>: <detail>"`` ``str()`` form) and falls
    back to ``str(exc)``.  The job task inherits the submitting request's
    context, so the fallback reads that request's language.
    """
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else (str(exc) or _JOB_FAILED.current)


class JobView(BaseModel):
    """Immutable, JSON-serialisable snapshot of a job, used as the wire model.

    The single shape the SSE feed and REST listing emit and the client
    renders.  Carries only generic fields so the same model serves every
    feature that submits jobs.

    ``created_at``/``updated_at`` are monotonic timestamps (:func:`time.monotonic`),
    used purely to order snapshots and drop stale ones.  They are never displayed as
    wall-clock time, so a backward clock adjustment cannot freeze a job's UI.
    """

    id: str
    kind: str
    title: str
    status: JobStatus
    created_at: float
    updated_at: float
    stage: str | None = None
    progress: JobProgress | None = None
    error: str | None = None


class FeedReady(BaseModel):
    """Sentinel marking the end of a subscription's initial replay.

    Emitted once, after every retained job has been replayed on connect and
    before any live change, so a (re)connecting client can tell the seed of
    current state apart from later transitions: it can show jobs that were
    already running without re-announcing them, and ignore jobs that finished
    before it connected instead of flashing a stale completion.
    """

    type: Literal["ready"] = "ready"


class ConversationsChanged(BaseModel):
    """Marks a change to the owner's conversations, which a client lists again."""

    type: Literal["conversations-changed"] = "conversations-changed"


class ConversationActivity(BaseModel):
    """Marks a turn starting or the delegated tasks changing in one of the owner's conversations.

    A client showing the conversation follows a turn it did not start, such as
    one a finished task woke, and reads the tasks again.
    """

    type: Literal["conversation-activity"] = "conversation-activity"
    conversation_id: UUID
    kind: Literal["turn", "tasks"]


class LibraryChanged(BaseModel):
    """Marks a change to a library the subscriber sees, whose list a client reads again."""

    type: Literal["library-changed"] = "library-changed"
    kind: Literal["skills", "assistants"]


class SourceChanged(BaseModel):
    """Marks a change to the sync state of a source, which an admin's client lists again."""

    type: Literal["source-changed"] = "source-changed"


type FeedEvent = (
    JobView
    | FeedReady
    | WorkspaceChanged
    | ConversationsChanged
    | ConversationActivity
    | LibraryChanged
    | SourceChanged
)


class _Job(JobView):
    """Mutable runtime record: a wire snapshot plus its control fields.

    Inherits every serialisable field from :class:`JobView` and adds the
    owner used for access control and the task used for cancellation,
    neither of which crosses the wire.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    owner: str
    spooled: int = 0
    task: asyncio.Task[None] | None = None

    def view(self) -> JobView:
        """Snapshot the runtime record as an independent wire model.

        A fresh :class:`JobView` is built every call so a queued snapshot
        stays frozen at its moment instead of tracking later mutations.
        """
        return JobView(**{name: getattr(self, name) for name in JobView.model_fields})


def _enqueue(queue: asyncio.Queue[FeedEvent], view: FeedEvent) -> None:
    """Enqueue *view*, dropping the oldest snapshot when a slow consumer is full.

    Every snapshot is full state, so a lagging feed still converges on the
    latest after the drop; the bound is purely a memory guard.
    """
    try:
        queue.put_nowait(view)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()

        queue.put_nowait(view)


@dataclass(slots=True, frozen=True)
class _Subscription:
    """Whose jobs a feed subscription receives, and the topics it follows by the names it knows them."""

    owners: frozenset[str]
    follows: Mapping[str, str]


@dataclass(slots=True, frozen=True)
class JobContext:
    """Handle a running job uses to report its progress.

    Cancellation is delivered as :class:`asyncio.CancelledError` raised
    into the work coroutine; the coroutine is responsible for rolling its
    own side effects back before re-raising.
    """

    _job: _Job
    _notify: Callable[[_Job], None]

    def set_stage(self, stage: str) -> None:
        """Set the human-readable stage label and broadcast the change."""
        self._job.stage = stage
        self._notify(self._job)

    def set_progress(self, current: int, total: int) -> None:
        """Set discrete progress, broadcasting only when the percent advances.

        The stored progress is always fresh, but a snapshot is pushed to
        subscribers only when the integer percent changes or the work
        completes, so a 10k-file job emits ~100 updates instead of 10k while
        still converging on the final count.
        """
        previous = self._job.progress
        self._job.progress = JobProgress(current=current, total=total)
        if (
            current >= total
            or previous is None
            or _percent(previous) != _percent(self._job.progress)
        ):
            self._notify(self._job)


type JobWork = Callable[[JobContext], Awaitable[None]]


@dataclass(slots=True, frozen=True, eq=False)
class Reservation:
    """The room of a job that has not been submitted yet, held until :meth:`JobManager.release`."""

    owner: str
    spooled: int


@dataclass(slots=True)
class JobManager:
    """In-memory registry that runs, tracks, and broadcasts jobs.

    Instantiable so tests get an isolated manager; the process shares the
    module-level :data:`manager` singleton.
    """

    max_concurrency: int = field(default_factory=lambda: settings.jobs.max_concurrency)
    retain_seconds: float = field(default_factory=lambda: settings.jobs.retain_seconds)
    queue_maxsize: int = field(default_factory=lambda: settings.jobs.queue_maxsize)
    max_owner_jobs: int = field(default_factory=lambda: settings.jobs.max_owner_jobs)
    max_owner_spool_bytes: int = field(
        default_factory=lambda: settings.jobs.max_owner_spool_bytes
    )
    # Per owner, its jobs by id in the order they came, so a lookup by id
    # never reaches another owner's job.
    _jobs: dict[str, dict[str, _Job]] = field(default_factory=dict, init=False)
    _subscribers: dict[asyncio.Queue[FeedEvent], _Subscription] = field(
        default_factory=dict, init=False
    )
    _reservations: list[Reservation] = field(default_factory=list, init=False)
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    def submit(
        self,
        *,
        kind: str,
        title: str,
        owner: User,
        work: JobWork,
        on_settled: Callable[[], None] | None = None,
        spooled: int = 0,
    ) -> JobView:
        """Register *work* as a job and start it; return its initial snapshot.

        The job runs as a detached task bounded by the concurrency limit,
        independent of any request, so a client disconnect never aborts it.
        *on_settled* runs once the job reaches a terminal state through any
        path (success, failure, or a cancel even before it started), so a
        feature can release resources tied to the job (e.g. a spooled payload
        of *spooled* bytes, which :meth:`admits` counts until then).
        It holds a lease of *owner*'s grants until it settled, which every
        request of *owner* renews (:class:`~hivegent.auth.Lease`), so one
        still queued once the lease lapsed fails instead of starting on grants
        no token vouches for.
        """
        self._prune()
        now = time.monotonic()
        job = _Job(
            id=str(uuid4()),
            kind=kind,
            title=title,
            owner=owner.id,
            spooled=spooled,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._jobs.setdefault(owner.id, {})[job.id] = job
        job.task = asyncio.create_task(self._run(job, work, owner))
        job.task.add_done_callback(partial(self._settle, job, on_settled))
        self.publish(owner.id, job.view())
        return job.view()

    def publish(self, owner: str, event: FeedEvent) -> None:
        """Send *event* to every subscription following *owner*'s jobs.

        Transient like :meth:`notify`.
        """
        for queue, subscription in self._subscribers.items():
            if owner in subscription.owners:
                _enqueue(queue, event)

    def broadcast(self, event: FeedEvent) -> None:
        """Send *event* to every subscription, transient like :meth:`notify`."""
        for queue in self._subscribers:
            _enqueue(queue, event)

    def notify(
        self, event_for: Callable[[Mapping[str, str]], FeedEvent | None]
    ) -> None:
        """Offer every subscription what *event_for* derives from the topics it follows.

        Transient: a reconnecting client re-reads the scopes it holds, so
        nothing missed while disconnected is lost.
        """
        for queue, subscription in self._subscribers.items():
            if (event := event_for(subscription.follows)) is not None:
                _enqueue(queue, event)

    async def _run(self, job: _Job, work: JobWork, owner: User) -> None:
        with leases.hold(owner) as lease:
            async with self._semaphore:
                if lease.expired:
                    raise PermissionError(_AUTHORIZATION_EXPIRED.current)

                job.status = "running"
                self._touch(job)
                await work(JobContext(job, self._touch))

    def _settle(
        self,
        job: _Job,
        on_settled: Callable[[], None] | None,
        task: asyncio.Task[None],
    ) -> None:
        """Record the terminal state of *job*, also when its task was cancelled before it started."""
        if task.cancelled():
            job.status = "cancelled"
        elif (exc := task.exception()) is not None:
            logger.error("Job %s (%s) failed", job.id, job.kind, exc_info=exc)
            job.status = "failed"
            job.error = _error_message(exc)
        else:
            job.status = "succeeded"

        if on_settled is not None:
            try:
                on_settled()
            except Exception:
                logger.exception("Job %s (%s) finalizer failed", job.id, job.kind)

        self._touch(job)
        self._prune()

    def cancel(self, owners: Collection[str], job_id: str) -> bool:
        """Request cancellation of a job of one of *owners*; return whether it was live."""
        job = next(
            (job for owner in owners if (job := self._jobs.get(owner, {}).get(job_id))),
            None,
        )
        if job is None or job.task is None:
            return False
        if job.status in _TERMINAL:
            return False
        job.task.cancel()
        return True

    def admits(self, owner: str, spooled: int) -> bool:
        """Whether *owner* may submit one more job spooling *spooled* bytes.

        Only unfinished jobs and reservations count, against ``max_owner_jobs``
        and, by the bytes they hold spooled, against ``max_owner_spool_bytes``.
        """
        held = [
            *(job.spooled for job in self._unfinished(owner)),
            *(r.spooled for r in self._reservations if r.owner == owner),
        ]

        return (
            len(held) < self.max_owner_jobs
            and sum(held) + spooled <= self.max_owner_spool_bytes
        )

    def reserve(self, owner: str, spooled: int) -> Reservation | None:
        """Hold the room :meth:`admits` found for a job spooling *spooled* bytes, ``None`` without any.

        Admitting and holding take one synchronous step, so concurrent requests
        cannot all pass before the first one submitted.  The holder submits its
        job and then calls :meth:`release`, with no await between, or on any error.
        """
        if not self.admits(owner, spooled):
            return None

        reservation = Reservation(owner, spooled)
        self._reservations.append(reservation)

        return reservation

    def release(self, reservation: Reservation) -> None:
        """Give back what *reservation* held."""
        self._reservations.remove(reservation)

    async def cancel_owner(self, owner: str) -> None:
        """Cancel every unfinished job of *owner* and wait until each settled."""
        tasks = [job.task for job in self._unfinished(owner) if job.task is not None]

        for task in tasks:
            task.cancel()

        if tasks:
            _ = await asyncio.wait(tasks)

    def list_jobs(self, owners: Collection[str]) -> list[JobView]:
        """Return every known job of *owners*, oldest first."""
        return [job.view() for job in self._owners_jobs(owners)]

    async def subscribe(
        self, owners: Collection[str], follows: Mapping[str, str] = MappingProxyType({})
    ) -> AsyncGenerator[FeedEvent]:
        """Yield the snapshots of *owners*' jobs: every retained job, a :class:`FeedReady`
        marker, then each change, and what :meth:`notify` offers *follows*.

        Both active and recently-settled jobs are replayed on connect: a client
        that dropped while a job finished would otherwise never receive the
        terminal transition, leaving its tray or an awaiting caller stuck on the
        last in-flight snapshot. Terminal jobs are retained (see :meth:`_prune`),
        so re-sending them lets a (re)connecting client converge on the true
        state; the client treats a terminal snapshot it already handled as a
        no-op, firing its settle handlers only on the first transition.

        The :class:`FeedReady` sentinel terminates the seed so the client can
        tell the replay of current state apart from later live transitions,
        e.g. to show already-running jobs without re-announcing them and to
        ignore jobs that finished before it connected.

        The subscriber is registered and seeded synchronously before the first
        suspension; the ``finally`` unsubscribes when the consumer closes the
        generator (e.g. via :func:`contextlib.aclosing`).
        """
        queue: asyncio.Queue[FeedEvent] = asyncio.Queue(maxsize=self.queue_maxsize)
        self._subscribers[queue] = _Subscription(frozenset(owners), follows)
        for job in self._owners_jobs(owners):
            _enqueue(queue, job.view())
        _enqueue(queue, FeedReady())
        try:
            while True:
                yield await queue.get()
        finally:
            del self._subscribers[queue]

    def _unfinished(self, owner: str) -> list[_Job]:
        """The jobs of *owner* that are still queued or running."""
        return [
            job
            for job in self._jobs.get(owner, {}).values()
            if job.status not in _TERMINAL
        ]

    def _owners_jobs(self, owners: Collection[str]) -> list[_Job]:
        jobs = (job for owner in owners for job in self._jobs.get(owner, {}).values())

        return sorted(jobs, key=lambda job: job.created_at)

    def _touch(self, job: _Job) -> None:
        job.updated_at = time.monotonic()
        self.publish(job.owner, job.view())

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.retain_seconds

        for owner, jobs in list(self._jobs.items()):
            for job_id, job in list(jobs.items()):
                if job.status in _TERMINAL and job.updated_at < cutoff:
                    del jobs[job_id]

            if not jobs:
                del self._jobs[owner]


manager = JobManager()
