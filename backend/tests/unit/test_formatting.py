"""Tests for the shared LLM-output formatting budgets."""

from hivegent.tools.formatting import cap_lines


class TestCapLines:
    """The whole-output budget that bounds what a tool return renders."""

    def test_keeps_contiguous_prefix(self) -> None:
        # A later short line must not backfill past a dropped one: callers
        # resume from where the output stops, so a gap would lose content.
        text, omitted = cap_lines(["aaaa", "bbbbbbbb", "c"], 6)
        assert text == "aaaa"
        assert omitted == 2

    def test_keeps_first_line_however_long(self) -> None:
        text, omitted = cap_lines(["x" * 500, "y"], 10)
        assert text == "x" * 500
        assert omitted == 1

    def test_unbounded_budget_keeps_everything(self) -> None:
        assert cap_lines(["a", "b"]) == ("a\nb", 0)
