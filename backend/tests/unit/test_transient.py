"""The transient folder every temporary file of the server lies below."""

import tempfile
from pathlib import Path

import pytest

from hivegent.files import reset_transient, transient


def test_the_boot_empties_the_transient_folder_and_makes_it_the_temporary_directory(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever a dead server left goes, and every later temporary file lands inside."""
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.delenv("TMPDIR", raising=False)
    root = transient()
    (root / "spool").mkdir(parents=True)
    _ = (root / "spool" / "upload-1").write_bytes(b"left")

    reset_transient()

    assert list(root.iterdir()) == []

    with tempfile.TemporaryDirectory() as made:
        assert Path(made).parent == root
