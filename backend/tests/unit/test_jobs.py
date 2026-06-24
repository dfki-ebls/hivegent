"""Unit tests for the generic background-job manager."""

import asyncio
from contextlib import aclosing

import pytest

from hivegent.server.jobs import FeedReady, JobContext, JobManager, JobView
from hivegent.server.operations.processing import run_bulk_document_job


async def _run_to_terminal(
    manager: JobManager, owner: str, job_id: str, timeout: float = 1.0
) -> JobView:
    """Drive the event loop until *job_id* reports a terminal status."""
    async with aclosing(manager.subscribe(owner)) as feed:
        while True:
            snap = await asyncio.wait_for(anext(feed), timeout)
            if isinstance(snap, FeedReady):
                continue
            if snap.id == job_id and snap.status in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                return snap


async def test_job_runs_reports_stage_and_succeeds() -> None:
    manager = JobManager(max_concurrency=2)
    stages: list[str] = []

    async def work(ctx: JobContext) -> None:
        ctx.set_stage("converting")
        stages.append("ran")
        ctx.set_progress(1, 1)

    view = manager.submit(
        kind="document.upload", title="a.pdf", owner="u1", scope="~", work=work
    )
    assert view.status == "queued"

    final = await _run_to_terminal(manager, "u1", view.id)
    assert final.status == "succeeded"
    assert final.stage == "converting"
    assert final.progress is not None and final.progress.current == 1
    assert stages == ["ran"]


async def test_job_cancel_marks_cancelled() -> None:
    manager = JobManager()

    async def work(_ctx: JobContext) -> None:
        await asyncio.sleep(10)

    view = manager.submit(kind="x", title="slow", owner="u1", scope=None, work=work)
    await asyncio.sleep(0.02)

    assert manager.cancel("u1", view.id) is True
    final = await _run_to_terminal(manager, "u1", view.id)
    assert final.status == "cancelled"
    # Cancelling a finished job, or another owner's job, is a no-op.
    assert manager.cancel("u1", view.id) is False
    assert manager.cancel("other", view.id) is False


async def test_job_failure_carries_message_and_owner_isolation() -> None:
    manager = JobManager()

    async def work(_ctx: JobContext) -> None:
        raise ValueError("boom")

    view = manager.submit(kind="x", title="f", owner="u1", scope=None, work=work)
    final = await _run_to_terminal(manager, "u1", view.id)
    assert final.status == "failed"
    assert final.error == "boom"

    # Jobs are scoped to their owner.
    assert manager.list_jobs("other") == []


async def test_subscribe_seeds_retained_jobs_then_ready_marker() -> None:
    """A new subscriber replays current jobs, then a FeedReady ends the seed.

    The marker lets a client tell the replay of current state apart from later
    live transitions, so it can ignore jobs that finished before it connected.
    """
    manager = JobManager()

    async def work(_ctx: JobContext) -> None:
        return None

    view = manager.submit(kind="x", title="done", owner="u1", scope=None, work=work)
    await _run_to_terminal(manager, "u1", view.id)

    async with aclosing(manager.subscribe("u1")) as feed:
        seed = await asyncio.wait_for(anext(feed), 1.0)
        assert isinstance(seed, JobView) and seed.id == view.id
        marker = await asyncio.wait_for(anext(feed), 1.0)
        assert isinstance(marker, FeedReady)


class _Progress:
    """Minimal ProgressReporter capturing the last reported counts."""

    def __init__(self) -> None:
        self.current = 0
        self.total = 0

    def set_stage(self, stage: str) -> None:
        pass

    def set_progress(self, current: int, total: int) -> None:
        self.current, self.total = current, total


async def test_bulk_runner_raises_so_failures_are_not_a_false_success() -> None:
    """A bulk batch with any failed file raises, so the job settles failed.

    The successful files are still processed; the raise carries the count and
    names so the tray shows the failure instead of a misleading green check.
    """
    progress = _Progress()
    processed: list[str] = []

    async def process_one(filepath: str) -> None:
        processed.append(filepath)
        if filepath == "b.md":
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as exc:
        await run_bulk_document_job(
            ["a.md", "b.md", "c.md"], process_one, verb="Rechunked", ctx=progress
        )

    assert processed == ["a.md", "b.md", "c.md"]  # batch is not aborted
    assert "1 failed" in str(exc.value)
    assert "b.md" in str(exc.value)
    assert (progress.current, progress.total) == (3, 3)
