"""Unit tests for the generic background-job manager."""

import asyncio

import pytest

from hivegent.server.jobs import JobContext, JobManager, JobView
from hivegent.server.operations.processing import run_bulk_document_job


async def _run_to_terminal(
    manager: JobManager, owner: str, job_id: str, timeout: float = 1.0
) -> JobView:
    """Drive the event loop until *job_id* reports a terminal status."""
    async with manager.subscribe(owner) as feed:
        while True:
            snap = await asyncio.wait_for(feed.__anext__(), timeout)
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
