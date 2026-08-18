"""Unit tests for the generic background-job manager."""

import asyncio
from contextlib import aclosing

import pytest

from hivegent.jobs import FeedReady, JobContext, JobManager, JobView, ScopeChanged
from hivegent.server.operations.processing import (
    run_bulk_document_job,
    summarize_failed_files,
)
from hivegent.types import FailedFile


async def _run_to_terminal(
    manager: JobManager, owner: str, job_id: str, timeout: float = 1.0
) -> JobView:
    """Drive the event loop until *job_id* reports a terminal status."""
    async with aclosing(manager.subscribe(owner)) as feed:
        while True:
            snap = await asyncio.wait_for(anext(feed), timeout)
            if not isinstance(snap, JobView):
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


def test_summarize_failed_files_groups_by_reason() -> None:
    """Files sharing a reason collapse to one clause; distinct reasons split."""
    summary = summarize_failed_files(
        [
            FailedFile(path="a.tex", reason="already in the workspace"),
            FailedFile(path="a.xlsm", reason="already in the workspace"),
            FailedFile(path="b.pdf", reason="conversion failed"),
        ]
    )

    assert summary == (
        "already in the workspace: a.tex, a.xlsm; conversion failed: b.pdf"
    )


async def test_scope_change_reaches_the_feed_without_becoming_a_job() -> None:
    manager = JobManager(max_concurrency=1)

    async with aclosing(manager.subscribe("u1")) as feed:
        assert isinstance(await anext(feed), FeedReady)
        manager.notify_scope_changed("u1", "@team")
        event = await asyncio.wait_for(anext(feed), 1.0)

    assert event == ScopeChanged(scope="@team")
    # Nothing to track or replay: a later subscriber sees only its own seed.
    assert manager.list_jobs("u1") == []


async def test_scope_change_skips_the_client_that_caused_it() -> None:
    """The acting tab re-reads on its own; the user's other tabs need telling."""
    manager = JobManager()

    async with (
        aclosing(manager.subscribe("u1", "tab-a")) as acting,
        aclosing(manager.subscribe("u1", "tab-b")) as other,
    ):
        assert isinstance(await anext(acting), FeedReady)
        assert isinstance(await anext(other), FeedReady)
        manager.notify_scope_changed("u1", "~", exclude_client="tab-a")

        assert await asyncio.wait_for(anext(other), 1.0) == ScopeChanged(scope="~")
        # Only the excluded tab was skipped, so its next event is the following
        # notification rather than the one it caused.
        manager.notify_scope_changed("u1", "@team")
        assert await asyncio.wait_for(anext(acting), 1.0) == ScopeChanged(scope="@team")
