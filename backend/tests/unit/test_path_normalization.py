"""Unit tests for the near-miss hint and which files reconciliation reads as entries."""

from pathlib import Path

from exar.tools.common import near_miss_hint

from hivegent.reconcile import _references


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


def test_vendor_directories_are_not_indexed(tmp_path: Path) -> None:
    """Reconciliation skips what a listing hides by default, ``.git`` included."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "notes.md").write_text("x")
    (tmp_path / "notes.md").write_text("# Notes")

    assert _references(tmp_path) == {"notes.md"}
