"""Tests for the rename journal a changeset installs through, and its recovery at boot."""

import os
from pathlib import Path

import pytest

from hivegent.reconcile import reconcile_all
from hivegent.store import HostGrant
from hivegent.tools.changes import ChangeRefused
from hivegent.workspace import kept_changes, paths, recover_changesets
from hivegent.workspace.paths import _Journal


def _journal(data_dir: Path) -> tuple[Path, _Journal]:
    root = data_dir / "workspace" / "user:u"
    root.mkdir(parents=True)
    _ = (root / "a.md").write_text("old")
    _ = (root / "b.md").write_text("kept")
    journal = _Journal(data_dir / "workspace" / ".stage-test")
    staged = journal.staging / "new" / "a.md"
    staged.parent.mkdir(parents=True)
    _ = staged.write_text("new")
    journal.install(staged, root / "a.md")
    journal.remove(root / "b.md")

    return root, journal


@pytest.mark.parametrize(
    ("xid", "committed"),
    [(None, False), ("7", False), ("7", True)],
    ids=["unprepared", "rows rolled back", "rows committed"],
)
async def test_the_boot_follows_the_rows_of_a_change_a_crash_interrupted(
    data_dir: Path,
    transaction_status: dict[str, bool],
    xid: str | None,
    committed: bool,
) -> None:
    """A change in flight is not kept, and once its rows are prepared the files follow their transaction."""
    root, journal = _journal(data_dir)

    if xid:
        journal.prepare(xid)
        transaction_status[xid] = committed

    assert kept_changes() == []

    await recover_changesets()

    assert (root / "a.md").read_text() == ("new" if committed else "old")
    assert (root / "b.md").exists() != committed
    assert not journal.staging.exists()
    assert not journal.log.exists()


def test_a_rollback_survives_failing_to_record_an_undo(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, journal = _journal(data_dir)

    def fail(self: _Journal, record: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_Journal, "_write", fail)
    journal.rollback()

    assert journal.close()
    assert (root / "a.md").read_text() == "old"
    assert not journal.log.exists()


async def test_a_failed_rollback_keeps_its_backups_for_the_boot_to_finish(
    data_dir: Path,
) -> None:
    """An undo never replaces a path written since, and stops there.

    Reconciliation is refused meanwhile, which would drop the parked file's row.
    """
    root, journal = _journal(data_dir)
    _ = (root / "b.md").write_text("written since")

    journal.rollback()

    assert not journal.close()
    assert (journal.staging / "backup").exists()
    assert kept_changes() == [journal.kept]

    with pytest.raises(ChangeRefused):
        _ = await reconcile_all(HostGrant())

    (root / "b.md").unlink()
    await recover_changesets()

    assert kept_changes() == []
    assert (root / "a.md").read_text() == "old"
    assert (root / "b.md").read_text() == "kept"
    assert not journal.staging.exists()


async def test_the_boot_keeps_a_change_whose_transaction_postgresql_forgot(
    data_dir: Path,
) -> None:
    root, journal = _journal(data_dir)
    journal.prepare("7")

    await recover_changesets()

    assert (root / "a.md").read_text() == "new"
    assert kept_changes() == [journal.kept]


def test_a_change_that_logged_no_step_commits_and_leaves_nothing(
    data_dir: Path,
) -> None:
    journal = _Journal(data_dir / "workspace" / ".stage-empty")
    journal.begin()

    journal.commit()

    assert journal.close()
    assert not journal.log.exists()


def test_the_boot_removes_only_the_staging_areas_no_journal_names(
    data_dir: Path,
) -> None:
    root, journal = _journal(data_dir)
    orphan = data_dir / "sources" / ".stage-orphan"
    (orphan / "new").mkdir(parents=True)

    paths._sweep_staging()

    assert not orphan.exists()
    assert journal.staging.exists()
    assert (root / "a.md").read_text() == "new"


def test_housekeeping_sweeps_old_partial_files_anywhere_but_spares_young_ones(
    data_dir: Path,
) -> None:
    now = 1_000_000.0
    old = data_dir / "conversations" / "u" / "c" / ".~0123456789abcdef.partial"
    young = data_dir / "workspace" / "user:u" / ".~fedcba9876543210.partial"

    for path, age in ((old, 2 * paths._PARTIAL_GRACE), (young, 1)):
        path.parent.mkdir(parents=True)
        _ = path.write_text("x")
        os.utime(path, (now - age, now - age))

    assert paths.sweep_partials(data_dir, now) == 1
    assert not old.exists() and young.exists()
