"""The read-only inventory walk tolerates concurrent workspace deletes."""

import errno
import os
from pathlib import Path

import pytest

from hivegent.server.operations import inventory as inv


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace with one stem entry (original + description) and a markdown."""
    (tmp_path / "laws2").write_text("raw", encoding="utf-8")
    (tmp_path / "laws2.md").write_text("# laws2", encoding="utf-8")
    (tmp_path / "keep.md").write_text("# keep", encoding="utf-8")
    return tmp_path


def test_file_vanishing_mid_scan_is_skipped_not_raised(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delete landing between listing and stat must not 500 the walk."""
    real_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "laws2":  # the original vanishes exactly when stat'd
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(self))
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky_stat)

    entries = inv._logical_entries_for_directory(workspace, workspace, {})
    # The entry survives via its description; the vanished original is dropped.
    assert {e.filename for e in entries} == {"laws2.md", "keep.md"}
    tree = inv._build_directory_tree(workspace, workspace, {})
    assert {c.path for c in (tree.children or [])} == {"laws2.md", "keep.md"}


def test_vanished_directory_yields_empty_listing(tmp_path: Path) -> None:
    """Walking a directory that no longer exists returns nothing, not an error."""
    assert inv._safe_iterdir(tmp_path / "ghost") == []
