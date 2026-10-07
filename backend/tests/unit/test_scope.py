"""Unit tests for the concrete WorkspaceScope grammar (~/@ convention)."""

from pathlib import Path

import pytest

from hivegent import entries
from hivegent.agents.common import UserDeps
from hivegent.changes import Changeset, Delete
from hivegent.server.common import parse_document_scope
from hivegent.store import Casebase, WorkspaceScope
from hivegent.tmp import tmp_dir
from hivegent.tools.base import SearchPath, resolve_accessible_file
from hivegent.types import DocumentFilter
from hivegent.workspace.operations import Location, route


def _deps(conversation_id: str | None) -> UserDeps:
    return UserDeps("u", Casebase.for_user("u"), "interactive", conversation_id)


class TestWorkspaceScope:
    """Render, strip, and parse for the ~/@ addressing convention."""

    def test_render_personal_and_group(self) -> None:
        assert WorkspaceScope().render("reports/q1.md") == "~/reports/q1.md"
        assert WorkspaceScope("team").render("notes.md") == "@team/notes.md"
        assert WorkspaceScope("team").render("") == "@team"

    def test_strip_recovers_local_or_none(self) -> None:
        personal = WorkspaceScope()
        assert personal.strip_prefix("~/reports") == "reports"
        assert personal.strip_prefix("~") == ""
        assert personal.strip_prefix("@team/x") is None
        assert personal.strip_prefix("reports") is None

    def test_parse_round_trips_render(self) -> None:
        for raw in ("~", "~/reports/q1.md", "@team", "@team/notes.md"):
            scope, local = WorkspaceScope.parse(raw)
            assert scope.render(local) == raw

    def test_parse_requires_a_prefix(self) -> None:
        with pytest.raises(ValueError):
            WorkspaceScope.parse("reports/q1.md")


class TestRoute:
    """Routing a canonical path to the root it names."""

    _roots = (Casebase.for_user("u"), Casebase.for_group("team"))

    def test_routes_to_each_addressed_root(self) -> None:
        routed = route(Changeset((Delete("~/notes.md"), Delete("@team/a.md"))), self._roots)

        assert routed.locations == (
            Location(self._roots[0], "notes.md"),
            Location(self._roots[1], "a.md"),
        )

    def test_rejects_a_root_outside_the_given_set(self) -> None:
        with pytest.raises(ValueError, match="No accessible workspace"):
            route(Changeset((Delete("@other/notes.md"),)), self._roots)


class TestConversationTmp:
    """`/tmp` is the conversation's own folder, outside every workspace."""

    def test_each_conversation_resolves_only_its_own_folder(self, data_dir: Path) -> None:
        for conversation in ("c1", "c2"):
            folder = tmp_dir(data_dir, conversation)
            folder.mkdir(parents=True)
            (folder / "state.json").write_text(conversation)

        for conversation, other in (("c1", "c2"), ("c2", "c1")):
            paths = _deps(conversation).search_paths()
            sp, local, absolute = resolve_accessible_file(
                paths, "/tmp/state.json"
            ) or pytest.fail("unresolved")
            assert sp.prefixed(local) == "/tmp/state.json"
            assert absolute.read_text() == conversation
            assert resolve_accessible_file(paths, f"/tmp/../{other}/state.json") is None

        # A caller without a conversation, such as MCP, has no `/tmp` at all.
        assert resolve_accessible_file(_deps(None).search_paths(), "/tmp/state.json") is None

        with pytest.raises(ValueError, match="Invalid conversation ID"):
            _ = tmp_dir(data_dir, "../c1")


class TestCaseFolding:
    """Paths compare the way the workspace's filesystem compares them."""

    def test_a_filter_is_asked_about_the_name_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hidden `secret.md` is not reachable as `SECRET.md` where case folds."""
        monkeypatch.setattr(entries, "folds_case", lambda _directory: True)
        (tmp_path / "secret.md").write_text("x")
        hidden = DocumentFilter(excluded=frozenset({"secret.md"}))
        paths = (SearchPath(tmp_path, WorkspaceScope(), hidden),)

        assert resolve_accessible_file(paths, "~/secret.md") is None
        assert resolve_accessible_file(paths, "~/SECRET.md") is None


class TestDocumentScopePrompt:
    """The selection the user makes with the eye toggle must reach the model.

    Nothing else ties a bare "in der Tabelle" to a file: the included half of
    the selection prunes no tool, so an unmentioned scope leaves the model with
    no idea which of the workspace's documents the question was about.
    """

    def _deps(self, included: list[str], excluded: list[str]) -> UserDeps:
        relevant, document_filter, group_filters = parse_document_scope(
            included, excluded, frozenset()
        )
        return UserDeps(
            user_id="u1",
            store=Casebase.for_user("u1"),
            mode="interactive",
            document_filter=document_filter,
            group_filters=group_filters,
            relevant_documents=relevant,
        )

    def test_included_documents_are_named(self) -> None:
        scope = self._deps(["~/projekte/tabelle.md"], []).describe_document_scope()

        assert "~/projekte/tabelle.md" in scope
        assert "these documents" in scope

    def test_excluded_documents_are_named(self) -> None:
        scope = self._deps([], ["~/geheim.md"]).describe_document_scope()

        assert "~/geheim.md" in scope

    def test_no_selection_renders_nothing(self) -> None:
        """An unscoped chat must not spend prompt on an empty block."""
        assert self._deps([], []).describe_document_scope() == ""
