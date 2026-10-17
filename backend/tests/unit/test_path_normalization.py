"""Unit tests for the NFC path-normalization sweep and the near-miss hint.

Every sweep assertion here reads the directory *listing* rather than calling
``exists()``.  macOS resolves both spellings of a name to the same inode, so an
``exists()`` check passes before the sweep has done anything and would prove
nothing on a dev machine; the stored name is what actually changes, and APFS
preserves whatever spelling it was handed.

Escapes rather than literals throughout: this file is saved precomposed, so
spelling both forms out would compare NFC with NFC.
"""

from pathlib import Path

import pytest

from hivegent.reconcile import _normalize_disk_paths
from hivegent.tools.base import near_miss_hint

_NFD = "SU\u0308VOA.md"
_NFC = "S\u00dcVOA.md"


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_renames_decomposed_names_to_nfc(tmp_path: Path) -> None:
    (tmp_path / _NFD).write_text("body")
    # Pins the premise the assertion below rests on.  A filesystem that
    # normalizes on write fails here with a clear reason rather than turning
    # the real assertion into a tautology.
    assert _names(tmp_path) == [_NFD]

    # A single entry also catches a sweep that copied instead of renaming.
    assert _normalize_disk_paths(tmp_path) == 1
    assert _names(tmp_path) == [_NFC]

    # Idempotent: the reindex endpoint advertises re-running as safe and cheap.
    assert _normalize_disk_paths(tmp_path) == 0


def test_renames_nested_entries_deepest_first(tmp_path: Path) -> None:
    # The directory and its child are both decomposed, so renaming the parent
    # first would invalidate the child's recorded path.
    nested = tmp_path / "SU\u0308B"
    nested.mkdir()
    (nested / _NFD).write_text("body")

    _normalize_disk_paths(tmp_path)

    assert _names(tmp_path) == ["S\u00dcB"]
    assert _names(tmp_path / "S\u00dcB") == [_NFC]


def test_keeps_both_spellings_when_the_target_is_taken(tmp_path: Path) -> None:
    (tmp_path / _NFD).write_text("decomposed")
    (tmp_path / _NFC).write_text("precomposed")
    if len(_names(tmp_path)) == 1:
        pytest.skip("filesystem is normalization-insensitive: the spellings alias")

    # The one branch that could destroy data: a genuine second file is never
    # overwritten, only skipped.
    assert _normalize_disk_paths(tmp_path) == 0
    assert (tmp_path / _NFD).read_text() == "decomposed"
    assert (tmp_path / _NFC).read_text() == "precomposed"


class TestNearMissHint:
    """Tests for the refusal hint that names an equivalent sibling."""

    def test_names_a_case_only_sibling(self, tmp_path: Path) -> None:
        # The bare "not found" a model got one step after seeing the file in a
        # listing was unactionable; naming the real spelling makes it fixable.
        (tmp_path / "Report.md").write_text("x")
        assert "Report.md" in near_miss_hint(tmp_path / "report.md")

    def test_empty_without_an_equivalent_sibling(self, tmp_path: Path) -> None:
        (tmp_path / "Report.md").write_text("x")
        assert near_miss_hint(tmp_path / "other.md") == ""

    def test_empty_when_the_parent_is_missing(self, tmp_path: Path) -> None:
        assert near_miss_hint(tmp_path / "gone" / "doc.md") == ""
