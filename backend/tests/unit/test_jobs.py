"""Unit tests for the generic background-job manager."""

import asyncio
from contextlib import aclosing

import pytest

from hivegent import auth, workspace_events
from hivegent.jobs import FeedReady, JobContext, JobManager, JobView, JobWork
from hivegent.jobs import manager as shared
from hivegent.server.operations.processing import (
    run_bulk_document_job,
    summarize_failed_files,
)
from hivegent.shared.l10n import Localized
from hivegent.store import GroupScope, HostGrant, User, UserScope
from hivegent.tools.changes import PathMove, WorkspaceChanged
from hivegent.types import FailedFile, ProgressReporter

U1 = User("u1")


async def _noop(_ctx: JobContext) -> None:
    return None


async def _forever(_ctx: JobContext) -> None:
    await asyncio.Event().wait()


async def _settle(
    manager: JobManager, work: JobWork, owner: User = U1
) -> tuple[JobView, JobView]:
    """Submit *work* and return its first snapshot and its terminal one."""
    settled = asyncio.Event()
    first = manager.submit(
        kind="x", title="t", owner=owner, work=work, on_settled=settled.set
    )
    await settled.wait()
    final = next(job for job in manager.list_jobs({owner.id}) if job.id == first.id)

    return first, final


async def test_job_runs_reports_stage_and_succeeds() -> None:
    async def work(ctx: JobContext) -> None:
        ctx.set_stage("converting")
        ctx.set_progress(1, 1)

    first, final = await _settle(JobManager(max_concurrency=2), work)

    assert first.status == "queued"
    assert final.status == "succeeded"
    assert final.stage == "converting"
    assert final.progress is not None and final.progress.current == 1


async def test_cancelling_a_running_job_settles_it_cancelled() -> None:
    manager = JobManager()
    started, settled = asyncio.Event(), asyncio.Event()

    async def work(_ctx: JobContext) -> None:
        started.set()
        await asyncio.Event().wait()

    view = manager.submit(
        kind="x", title="t", owner=U1, work=work, on_settled=settled.set
    )
    await started.wait()

    assert manager.cancel({"other"}, view.id) is False
    assert manager.cancel({"u1"}, view.id) is True

    await settled.wait()

    assert manager.list_jobs({"u1"})[0].status == "cancelled"
    assert manager.cancel({"u1"}, view.id) is False


async def test_job_failure_carries_its_message_and_stays_with_its_owner() -> None:
    async def work(_ctx: JobContext) -> None:
        raise ValueError("boom")

    manager = JobManager()
    _, final = await _settle(manager, work)

    assert (final.status, final.error) == ("failed", "boom")
    assert manager.list_jobs({"other"}) == []


async def test_unfinished_jobs_bound_admission_until_cancelled() -> None:
    manager = JobManager(max_owner_jobs=2, max_owner_spool_bytes=100)
    settled: list[str] = []
    _ = manager.submit(kind="x", title="a", owner=U1, work=_forever, spooled=60)

    assert manager.admits("u1", 40)
    assert not manager.admits("u1", 41)
    assert manager.admits("u2", 100)

    _ = manager.submit(
        kind="x",
        title="b",
        owner=U1,
        work=_forever,
        on_settled=lambda: settled.append("b"),
    )

    assert not manager.admits("u1", 0)

    await manager.cancel_owner("u1")

    assert settled == ["b"]
    assert {job.status for job in manager.list_jobs({"u1"})} == {"cancelled"}
    assert manager.admits("u1", 100)


def test_reservations_bound_admission_until_released() -> None:
    manager = JobManager(max_owner_jobs=2, max_owner_spool_bytes=100)

    first = manager.reserve("u1", 60)

    assert first is not None
    assert manager.reserve("u1", 41) is None
    assert manager.reserve("u2", 100) is not None
    assert manager.reserve("u1", 40) is not None
    assert manager.reserve("u1", 0) is None

    manager.release(first)

    assert manager.reserve("u1", 0) is not None


async def test_a_job_queued_past_its_deadline_never_starts() -> None:
    """Its grants expired with the token that queued it, so it fails without running."""
    ran: list[str] = []

    async def work(_ctx: JobContext) -> None:
        ran.append("ran")

    _, final = await _settle(JobManager(), work, User("u1", expires_at=0))

    assert final.status == "failed" and ran == []


async def test_a_request_renews_a_queued_job_if_its_token_holds_the_grants() -> None:
    """A fresh token of the owner lets a job queued past the old one start, a narrower one does not."""
    manager = JobManager(max_concurrency=1)
    blocked, gate = asyncio.Event(), asyncio.Event()
    settled = {"u1": asyncio.Event(), "u2": asyncio.Event()}
    later = 10**10

    async def block(_ctx: JobContext) -> None:
        blocked.set()
        _ = await gate.wait()

    _ = manager.submit(kind="x", title="b", owner=User("u0"), work=block)
    owners = {
        "u1": User("u1", expires_at=0),
        "u2": User("u2", read_groups=frozenset({"eng"}), expires_at=0),
    }

    for name, owner in owners.items():
        _ = manager.submit(
            kind="x", title="f", owner=owner, work=_noop, on_settled=settled[name].set
        )

    # Tasks start in submission order, so both queued ones hold their lease by now.
    await blocked.wait()
    auth.leases.renew(User("u1", expires_at=later))
    auth.leases.renew(User("u2", expires_at=later))
    gate.set()
    _ = await asyncio.gather(*(event.wait() for event in settled.values()))

    assert manager.list_jobs({"u1"})[0].status == "succeeded"
    assert manager.list_jobs({"u2"})[0].status == "failed"


async def test_settled_jobs_are_retained_until_their_retention_passed() -> None:
    now = 0.0
    manager = JobManager(retain_seconds=10, clock=lambda: now)
    ids: list[str] = []

    for now in (0.0, 5.0, 11.0):
        first, _ = await _settle(manager, _noop)
        ids.append(first.id)

    assert [job.id for job in manager.list_jobs({"u1"})] == ids[1:]


async def test_subscribe_seeds_retained_jobs_then_ready_marker() -> None:
    """A new subscriber replays current jobs, then a FeedReady ends the seed."""
    manager = JobManager()
    view, _ = await _settle(manager, _noop)

    async with aclosing(manager.subscribe({"u1"})) as feed:
        seed = await anext(feed)

        assert isinstance(seed, JobView) and seed.id == view.id
        assert isinstance(await anext(feed), FeedReady)


async def test_bulk_runner_raises_so_failures_are_not_a_false_success() -> None:
    """A bulk batch with any failed file still processes the rest, then raises with the count and names."""
    reported: list[tuple[int, int]] = []
    processed: list[str] = []

    class Progress:
        def set_stage(self, stage: str) -> None:
            pass

        def set_progress(self, current: int, total: int) -> None:
            reported.append((current, total))

    async def process_one(filepath: str, _ctx: ProgressReporter) -> None:
        processed.append(filepath)

        if filepath == "b.md":
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match=r"1 failed.*b\.md"):
        await run_bulk_document_job(
            ["a.md", "b.md", "c.md"],
            process_one,
            verb=Localized(en="Rechunked", de="Neu gechunkt"),
            ctx=Progress(),
        )

    assert processed == ["a.md", "b.md", "c.md"]
    assert reported[-1] == (3, 3)


def test_summarize_failed_files_groups_by_reason() -> None:
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
    manager = JobManager()
    changed = WorkspaceChanged(("@team",))

    async with aclosing(manager.subscribe({"u1"})) as feed:
        assert isinstance(await anext(feed), FeedReady)
        manager.notify(lambda _follows: changed)

        assert await anext(feed) == changed

    assert manager.list_jobs({"u1"}) == []


async def test_workspace_change_reaches_every_follower_as_it_sees_it() -> None:
    """The actor sees all of it, another member only the group, an outsider nothing."""
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
            shared.subscribe({"u1"}, {"user:u1": "~", "group:team": "@team"})
        ) as u1,
        aclosing(
            shared.subscribe({"u2"}, {"user:u2": "~", "group:team": "@team"})
        ) as u2,
        aclosing(shared.subscribe({"u3"}, {"user:u3": "~"})) as u3,
    ):
        for feed in (u1, u2, u3):
            assert isinstance(await anext(feed), FeedReady)

        workspace_events.announce_workspace_changed((actor, team), changed)
        workspace_events.announce_workspace_changed(
            (HostGrant().workspace(UserScope("u3")),)
        )

        assert await anext(u1) == changed
        seen = await anext(u2)

        assert seen == WorkspaceChanged(("@team",), (), ("@team/c.md", "@team/b.md"))
        assert isinstance(seen, WorkspaceChanged) and seen.id == changed.id
        assert await anext(u3) == WorkspaceChanged(("~",))
