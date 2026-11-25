"""Unit tests for the generic background-job manager."""

import asyncio
import time
from contextlib import aclosing

import pytest

from hivegent import auth, workspace_events
from hivegent.jobs import FeedReady, JobContext, JobManager, JobView
from hivegent.server.operations.processing import (
    run_bulk_document_job,
    summarize_failed_files,
)
from hivegent.shared.l10n import Localized
from hivegent.store import GroupScope, HostGrant, User, UserScope
from hivegent.tools.changes import PathMove, WorkspaceChanged
from hivegent.types import FailedFile, ProgressReporter


async def _run_to_terminal(
    manager: JobManager, owner: str, job_id: str, timeout: float = 1.0
) -> JobView:
    """Drive the event loop until *job_id* reports a terminal status."""
    async with aclosing(manager.subscribe({owner})) as feed:
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
        kind="document.upload", title="a.pdf", owner=User("u1"), work=work
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

    view = manager.submit(kind="x", title="slow", owner=User("u1"), work=work)

    assert manager.cancel({"u1"}, view.id) is True
    final = await _run_to_terminal(manager, "u1", view.id)
    assert final.status == "cancelled"
    # Cancelling a finished job, or another owner's job, is a no-op.
    assert manager.cancel({"u1"}, view.id) is False
    assert manager.cancel({"other"}, view.id) is False


async def test_unfinished_jobs_bound_admission_until_cancelled() -> None:
    manager = JobManager(max_owner_jobs=2, max_owner_spool_bytes=100)
    settled: list[str] = []

    async def work(_ctx: JobContext) -> None:
        await asyncio.sleep(10)

    manager.submit(kind="x", title="a", owner=User("u1"), work=work, spooled=60)

    assert manager.admits("u1", 40)
    assert not manager.admits("u1", 41)
    assert manager.admits("u2", 100)

    manager.submit(
        kind="x",
        title="b",
        owner=User("u1"),
        work=work,
        on_settled=lambda: settled.append("b"),
    )

    assert not manager.admits("u1", 0)

    await manager.cancel_owner("u1")

    assert settled == ["b"]
    assert {job.status for job in manager.list_jobs({"u1"})} == {"cancelled"}
    assert manager.admits("u1", 100)


async def test_reservations_bound_admission_until_released() -> None:
    manager = JobManager(max_owner_jobs=2, max_owner_spool_bytes=100)

    first = manager.reserve("u1", 60)

    assert first is not None
    assert manager.reserve("u1", 41) is None
    assert manager.reserve("u2", 100) is not None

    second = manager.reserve("u1", 40)

    assert second is not None
    assert manager.reserve("u1", 0) is None

    manager.release(first)

    assert manager.reserve("u1", 0) is not None


async def test_job_failure_carries_message_and_owner_isolation() -> None:
    manager = JobManager()

    async def work(_ctx: JobContext) -> None:
        raise ValueError("boom")

    view = manager.submit(kind="x", title="f", owner=User("u1"), work=work)
    final = await _run_to_terminal(manager, "u1", view.id)
    assert final.status == "failed"
    assert final.error == "boom"

    # Jobs are scoped to their owner.
    assert manager.list_jobs({"other"}) == []


async def test_a_job_queued_past_its_deadline_never_starts() -> None:
    """Its grants expired with the token that queued it, so it fails without running."""
    manager = JobManager()
    ran: list[str] = []

    async def work(_ctx: JobContext) -> None:
        ran.append("ran")

    view = manager.submit(
        kind="x", title="f", owner=User("u1", expires_at=0), work=work
    )
    final = await _run_to_terminal(manager, "u1", view.id)

    assert final.status == "failed" and ran == []


async def test_a_request_renews_a_queued_job_unless_its_token_lacks_the_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh token of the owner lets a job queued past the old one start, a narrower one does not."""
    monkeypatch.setattr(auth, "_RENEWAL_SECONDS", 0)
    manager = JobManager(max_concurrency=1)
    gate = asyncio.Event()
    eng = frozenset({"eng"})

    async def block(_ctx: JobContext) -> None:
        _ = await gate.wait()

    async def work(_ctx: JobContext) -> None:
        pass

    def queue(owner: User) -> str:
        return manager.submit(kind="x", title="f", owner=owner, work=work).id

    _ = manager.submit(kind="x", title="b", owner=User("u0"), work=block)
    renewed = queue(User("u1", expires_at=time.time() + 0.05))
    lapsed = queue(User("u2", read_groups=eng, expires_at=time.time() + 0.05))
    await asyncio.sleep(0.1)

    auth.leases.renew(User("u1", expires_at=time.time() + 3600))
    auth.leases.renew(User("u2", expires_at=time.time() + 3600))
    gate.set()

    assert (await _run_to_terminal(manager, "u1", renewed)).status == "succeeded"
    assert (await _run_to_terminal(manager, "u2", lapsed)).status == "failed"


async def test_subscribe_seeds_retained_jobs_then_ready_marker() -> None:
    """A new subscriber replays current jobs, then a FeedReady ends the seed.

    The marker lets a client tell the replay of current state apart from later
    live transitions, so it can ignore jobs that finished before it connected.
    """
    manager = JobManager()

    async def work(_ctx: JobContext) -> None:
        return None

    view = manager.submit(kind="x", title="done", owner=User("u1"), work=work)
    await _run_to_terminal(manager, "u1", view.id)

    async with aclosing(manager.subscribe({"u1"})) as feed:
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

    async def process_one(filepath: str, _ctx: ProgressReporter) -> None:
        processed.append(filepath)
        if filepath == "b.md":
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as exc:
        await run_bulk_document_job(
            ["a.md", "b.md", "c.md"],
            process_one,
            verb=Localized(en="Rechunked", de="Neu gechunkt"),
            ctx=progress,
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


async def test_workspace_change_reaches_the_feed_without_becoming_a_job() -> None:
    manager = JobManager(max_concurrency=1)
    changed = WorkspaceChanged(("@team",))

    async with aclosing(manager.subscribe({"u1"})) as feed:
        assert isinstance(await anext(feed), FeedReady)
        manager.notify(lambda _follows: changed)
        event = await asyncio.wait_for(anext(feed), 1.0)

    assert event == changed
    # Nothing to track or replay: a later subscriber sees only its own seed.
    assert manager.list_jobs({"u1"}) == []


async def test_workspace_change_reaches_every_follower_as_it_sees_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actor sees all of it, another member only the group, an outsider nothing."""
    manager = JobManager()
    monkeypatch.setattr(workspace_events, "manager", manager)
    actor, team = (
        HostGrant().workspace(UserScope("u1")),
        HostGrant().workspace(GroupScope("team")),
    )
    changed = WorkspaceChanged(
        ("@team", "~"),
        (PathMove("~/a.md", "@team/a.md"), PathMove("@team/b.md", "~/b.md")),
        ("@team/c.md",),
    )

    async with (
        aclosing(
            manager.subscribe({"u1"}, {"user:u1": "~", "group:team": "@team"})
        ) as u1,
        aclosing(
            manager.subscribe({"u2"}, {"user:u2": "~", "group:team": "@team"})
        ) as u2,
        aclosing(manager.subscribe({"u3"}, {"user:u3": "~"})) as u3,
    ):
        for feed in (u1, u2, u3):
            assert isinstance(await anext(feed), FeedReady)

        workspace_events.announce_workspace_changed((actor, team), changed)
        workspace_events.announce_workspace_changed(
            (HostGrant().workspace(UserScope("u3")),)
        )

        assert await asyncio.wait_for(anext(u1), 1.0) == changed
        seen = await asyncio.wait_for(anext(u2), 1.0)
        assert seen == WorkspaceChanged(("@team",), (), ("@team/c.md", "@team/b.md"))
        assert isinstance(seen, WorkspaceChanged) and seen.id == changed.id
        assert await asyncio.wait_for(anext(u3), 1.0) == WorkspaceChanged(("~",))
