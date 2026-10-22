"""Tests for the shared LLM-output formatting budgets."""

from collections.abc import Callable

import pytest

from hivegent.tools.formatting import fit_lines, truncate_middle


def _read_on(head: str) -> str:
    return f"[read on from line {len(head.splitlines()) + 1}]"


@pytest.mark.parametrize("max_chars", [0, 1, 501, 1_000])
@pytest.mark.parametrize("note", [None, _read_on])
def test_a_middle_cut_stays_within_its_budget(
    max_chars: int, note: Callable[[str], str] | None
) -> None:
    assert len(truncate_middle("line\n" * 1_000, max_chars, note)) <= max_chars


def test_a_middle_cut_keeps_its_note_whole() -> None:
    note = "[the rest is saved at `/tmp/.tool-results/grep-0123abcd.txt`]"

    assert note in truncate_middle("line\n" * 1_000, len(note) + 2, lambda _head: note)


class TestFitLines:
    """The budget that bounds how many lines a tool takes."""

    def test_keeps_contiguous_prefix(self) -> None:
        # A later short line must not backfill past a dropped one: callers
        # resume from where the output stops, so a gap would lose content.
        assert fit_lines(["aaaa", "bbbbbbbb", "c"], 6) == 1

    def test_keeps_first_line_however_long(self) -> None:
        assert fit_lines(["x" * 500, "y"], 10) == 1

    def test_stops_reading_at_the_first_overflow(self) -> None:
        lines = iter(["aa", "bb", "cc", "dd"])
        assert fit_lines(lines, 4) == 1
        assert list(lines) == ["cc", "dd"]
