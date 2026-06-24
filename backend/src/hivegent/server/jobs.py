"""Generic in-process background-job framework.

A reusable layer for running asynchronous work off the request path with
live status, bounded concurrency, cooperative cancellation, and a
per-owner event feed.  A feature submits a coroutine plus metadata; the
manager runs it as an :class:`asyncio.Task`, tracks its lifecycle, and
broadcasts every state change to subscribers (the SSE feed consumes
these).

The registry is in-memory only: jobs do not survive a process restart.
Each feature keeps its own durable state consistent when a job is
cancelled or fails — e.g. the document pipeline rolls its workspace and
SQL writes back.  The framework itself knows nothing about documents; it
carries only generic, JSON-serialisable status fields so any subsystem
can reuse it.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..config import settings

__all__ = [
    "FeedEvent",
    "FeedReady",
    "JobContext",
    "JobManager",
    "JobProgress",
    "JobStatus",
    "JobView",
    "JobWork",
    "manager",
]

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


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
    back to ``str(exc)``.
    """
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else (str(exc) or "Job failed")


class JobView(BaseModel):
    """Immutable, JSON-serialisable snapshot of a job — the wire model.

    The single shape the SSE feed and REST listing emit and the client
    renders.  Carries only generic fields so the same model serves every
    feature that submits jobs.

    ``created_at``/``updated_at`` are monotonic timestamps (:func:`time.monotonic`),
    used purely to order snapshots and drop stale ones — never displayed as
    wall-clock time — so a backward clock adjustment cannot freeze a job's UI.
    """

    id: str
    kind: str
    title: str
    scope: str | None
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


type FeedEvent = JobView | FeedReady


class _Job(JobView):
    """Mutable runtime record: a wire snapshot plus its control fields.

    Inherits every serialisable field from :class:`JobView` and adds the
    owner used for access control and the task used for cancellation,
    neither of which crosses the wire.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    owner: str
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
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        queue.put_nowait(view)


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


@dataclass(slots=True)
class JobManager:
    """In-memory registry that runs, tracks, and broadcasts jobs.

    Instantiable so tests get an isolated manager; the process shares the
    module-level :data:`manager` singleton.
    """

    max_concurrency: int = 2
    retain_seconds: float = 3600.0
    queue_maxsize: int = 1024
    _jobs: dict[str, _Job] = field(default_factory=dict, init=False)
    _subscribers: dict[str, set[asyncio.Queue[FeedEvent]]] = field(
        default_factory=dict, init=False
    )
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    def submit(
        self,
        *,
        kind: str,
        title: str,
        owner: str,
        scope: str | None,
        work: JobWork,
        on_settled: Callable[[], None] | None = None,
    ) -> JobView:
        """Register *work* as a job and start it; return its initial snapshot.

        The job runs as a detached task bounded by the concurrency limit,
        independent of any request, so a client disconnect never aborts it.
        *on_settled* runs once the job reaches a terminal state through any
        path — success, failure, or a cancel even before it started — so a
        feature can release resources tied to the job (e.g. a spooled payload).
        """
        self._prune()
        now = time.monotonic()
        job = _Job(
            id=uuid4().hex,
            kind=kind,
            title=title,
            owner=owner,
            scope=scope,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job, work, on_settled))
        self._publish(job)
        return job.view()

    async def _run(
        self, job: _Job, work: JobWork, on_settled: Callable[[], None] | None
    ) -> None:
        """Drive one job through its lifecycle and record the terminal state."""
        ctx = JobContext(job, self._touch)
        try:
            async with self._semaphore:
                job.status = "running"
                self._touch(job)
                await work(ctx)
        except asyncio.CancelledError:
            job.status = "cancelled"
        except Exception as exc:
            logger.exception("Job %s (%s) failed", job.id, job.kind)
            job.status = "failed"
            job.error = _error_message(exc)
        else:
            job.status = "succeeded"
        finally:
            if on_settled is not None:
                try:
                    on_settled()
                except Exception:
                    logger.exception("Job %s (%s) finalizer failed", job.id, job.kind)

        self._touch(job)
        self._prune()

    def cancel(self, owner: str, job_id: str) -> bool:
        """Request cancellation of *owner*'s job; return whether it was live."""
        job = self._jobs.get(job_id)
        if job is None or job.owner != owner or job.task is None:
            return False
        if job.status in _TERMINAL:
            return False
        job.task.cancel()
        return True

    def list_jobs(self, owner: str) -> list[JobView]:
        """Return every known job for *owner*, oldest first."""
        return [job.view() for job in self._owner_jobs(owner)]

    async def subscribe(self, owner: str) -> AsyncGenerator[FeedEvent]:
        """Yield *owner*'s snapshots: every retained job, a :class:`FeedReady`
        marker, then each change.

        Both active and recently-settled jobs are replayed on connect: a client
        that dropped while a job finished would otherwise never receive the
        terminal transition, leaving its tray or an awaiting caller stuck on the
        last in-flight snapshot. Terminal jobs are retained (see :meth:`_prune`),
        so re-sending them lets a (re)connecting client converge on the true
        state; the client treats a terminal snapshot it already handled as a
        no-op, firing its settle handlers only on the first transition.

        The :class:`FeedReady` sentinel terminates the seed so the client can
        tell the replay of current state apart from later live transitions —
        e.g. to show already-running jobs without re-announcing them and to
        ignore jobs that finished before it connected.

        The subscriber is registered and seeded synchronously before the first
        suspension; the ``finally`` unsubscribes when the consumer closes the
        generator (e.g. via :func:`contextlib.aclosing`).
        """
        queue: asyncio.Queue[FeedEvent] = asyncio.Queue(maxsize=self.queue_maxsize)
        self._subscribers.setdefault(owner, set()).add(queue)
        for job in self._owner_jobs(owner):
            _enqueue(queue, job.view())
        _enqueue(queue, FeedReady())
        try:
            while True:
                yield await queue.get()
        finally:
            self._unsubscribe(owner, queue)

    def _owner_jobs(self, owner: str) -> list[_Job]:
        return sorted(
            (job for job in self._jobs.values() if job.owner == owner),
            key=lambda job: job.created_at,
        )

    def _touch(self, job: _Job) -> None:
        job.updated_at = time.monotonic()
        self._publish(job)

    def _publish(self, job: _Job) -> None:
        view = job.view()
        for queue in self._subscribers.get(job.owner, ()):
            _enqueue(queue, view)

    def _unsubscribe(self, owner: str, queue: asyncio.Queue[FeedEvent]) -> None:
        subscribers = self._subscribers.get(owner)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[owner]

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.retain_seconds
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in _TERMINAL and job.updated_at < cutoff
        ]
        for job_id in stale:
            del self._jobs[job_id]


manager = JobManager(
    max_concurrency=settings.jobs.max_concurrency,
    retain_seconds=settings.jobs.retain_seconds,
    queue_maxsize=settings.jobs.queue_maxsize,
)
