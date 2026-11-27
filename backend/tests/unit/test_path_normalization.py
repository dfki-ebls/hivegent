"""Unit tests for the near-miss hint and which files reconciliation reads as entries."""

from pathlib import Path

import pytest
from exar.tools.common import near_miss_hint

from hivegent.reconcile import _references


@pytest.mark.parametrize("missing", ["other.md", "gone/doc.md"])
def test_the_hint_is_empty_without_an_equivalent_sibling(
    tmp_path: Path, missing: str
) -> None:
    (tmp_path / "Report.md").write_text("x")

    assert near_miss_hint(tmp_path / missing) == ""


def test_the_hint_names_a_case_only_sibling(tmp_path: Path) -> None:
    (tmp_path / "Report.md").write_text("x")

    assert "Report.md" in near_miss_hint(tmp_path / "report.md")


def test_vendor_directories_are_not_indexed(tmp_path: Path) -> None:
    """Reconciliation skips what a listing hides by default, ``.git`` included."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "notes.md").write_text("x")
    (tmp_path / "notes.md").write_text("# Notes")

    assert _references(tmp_path) == {"notes.md"}
