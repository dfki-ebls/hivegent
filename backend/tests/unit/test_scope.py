"""Unit tests for the concrete WorkspaceScope grammar (~/@ convention)."""

import pytest

from hivegent.store import WorkspaceScope


class TestWorkspaceScope:
    """Render, strip, and parse for the ~/@ addressing convention."""

    def test_render_personal_and_group(self) -> None:
        assert WorkspaceScope().render("reports/q1.md") == "~/reports/q1.md"
        assert WorkspaceScope("team").render("notes.md") == "@team/notes.md"
        assert WorkspaceScope("team").render("") == "@team"

    def test_strip_recovers_local_or_none(self) -> None:
        personal = WorkspaceScope()
        assert personal.strip("~/reports") == "reports"
        assert personal.strip("~") == ""
        assert personal.strip("@team/x") is None
        assert personal.strip("reports") is None

    def test_parse_round_trips_render(self) -> None:
        for raw in ("~", "~/reports/q1.md", "@team", "@team/notes.md"):
            scope, local = WorkspaceScope.parse(raw)
            assert scope.render(local) == raw

    def test_parse_requires_a_prefix(self) -> None:
        with pytest.raises(ValueError):
            WorkspaceScope.parse("reports/q1.md")
