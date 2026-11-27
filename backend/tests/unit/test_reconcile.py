"""The filesystem is authoritative for content.

Reconciliation ingests on-disk markdown into SQL and drops rows whose
description vanished, but it never deletes or renames workspace files, and a
file without a description stays inert content rather than an orphan to prune.
"""

from pathlib import Path

import pytest

from hivegent import reconcile
from hivegent.store import Workspace
from tests.helpers import FakeIndex, entry


@pytest.fixture()
def workspace_dir(user_store: Workspace) -> Path:
    path = user_store.workspace_dir()
    path.mkdir(parents=True, exist_ok=True)

    return path


async def test_reconcile_ingests_markdown_and_keeps_every_other_file(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex
) -> None:
    """A file dropped by hand is no entry until it is uploaded, and keeps its name."""
    index.add(user_store, entry("doc"))
    files = ("doc.md", "notes.md", "loose.bin", "settings.ini", "gone.assets/fig.png")

    for path in files:
        (workspace_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace_dir / path).write_bytes(b"x")

    assert await reconcile.reconcile_store(user_store) == 2

    assert sorted(e.description_path for e in index.indexed) == ["doc.md", "notes.md"]
    assert all((workspace_dir / path).exists() for path in files)


async def test_reconcile_skips_a_failing_entry_and_ingests_the_rest(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex
) -> None:
    index.failing = {"bad"}

    for name in ("good.md", "bad.md"):
        (workspace_dir / name).write_text("x")

    assert await reconcile.reconcile_store(user_store) == 1
    assert [e.description_path for e in index.indexed] == ["good.md"]


async def test_reconcile_drops_rows_whose_description_vanished(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex
) -> None:
    index.add(user_store, entry("ghost"))

    assert await reconcile.reconcile_store(user_store) == 1
    assert index.rows == {}


@pytest.mark.parametrize("unreachable", ["root", "folder"])
async def test_reconcile_keeps_every_row_of_a_store_it_cannot_list(
    user_store: Workspace, workspace_dir: Path, index: FakeIndex, unreachable: str
) -> None:
    """An unmounted or unreadable store fails loudly rather than reading as empty."""
    index.add(user_store, entry("doc"))

    if unreachable == "root":
        workspace_dir.rmdir()
    else:
        (workspace_dir / "locked").mkdir(mode=0)

    try:
        with pytest.raises(OSError):
            _ = await reconcile.reconcile_store(user_store)
    finally:
        if unreachable == "folder":
            (workspace_dir / "locked").chmod(0o700)

    assert index.stems(user_store) == ["doc"]
