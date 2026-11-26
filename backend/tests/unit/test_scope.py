"""Unit tests for the scope grammar (~, @<group>, /srv) and its routing."""

from pathlib import Path

import pytest
from exar.memory.mounts import render, strip

from hivegent.agents.common import UserDeps
from hivegent.server.common import parse_document_scope
from hivegent.store import (
    GlobalScope,
    GroupScope,
    HostGrant,
    User,
    UserScope,
    parse_scope,
    split_scope_path,
)
from hivegent.tmp import tmp_dir
from hivegent.tools.base import SearchPath, resolve_accessible_file
from hivegent.tools.changes import Changeset, Delete
from hivegent.types import DocumentFilter, FilterEntry
from hivegent.workspace.operations import Location, Root, route
from tests.helpers import doc, label_id, owned


def _deps(conversation_id: str | None) -> UserDeps:
    return UserDeps(
        User("u"),
        "interactive",
        None if conversation_id is None else owned(conversation_id),
    )


class TestScope:
    """Render, strip, and parse for every scope's key and path prefix."""

    def test_render_each_scope(self) -> None:
        assert UserScope("u").render("reports/q1.md") == "~/reports/q1.md"
        assert GroupScope("team").render("notes.md") == "@team/notes.md"
        assert GlobalScope().render("") == "/srv"

    def test_strip_recovers_local_or_none(self) -> None:
        user, shared = UserScope("u").prefix, GlobalScope().prefix
        assert strip(user, "~/reports") == "reports"
        assert strip(user, "~") == ""
        assert strip(user, "@team/x") is None
        assert strip(shared, "/globals/x") is None

    def test_split_round_trips_render(self) -> None:
        for raw in ("~", "~/reports/q1.md", "@team", "@team/notes.md", "/srv/a"):
            prefix, local = split_scope_path(raw)
            assert render(prefix, local) == raw

    def test_split_requires_a_prefix(self) -> None:
        for raw in ("reports/q1.md", "/tmp/a", "@"):
            with pytest.raises(ValueError):
                split_scope_path(raw)

    def test_keys_parse_back(self) -> None:
        for scope in (GlobalScope(), GroupScope("team"), UserScope("u")):
            assert parse_scope(scope.key) == scope

        with pytest.raises(ValueError):
            parse_scope("tenant:x")


class TestRoute:
    """Routing a canonical path to the root it names."""

    _roots = (
        Root.of(HostGrant().workspace(UserScope("u"))),
        Root.of(HostGrant().workspace(GroupScope("team"))),
    )

    def test_routes_to_each_addressed_root(self) -> None:
        routed = route(
            Changeset((Delete("~/notes.md"), Delete("@team/a.md"))), self._roots
        )

        assert routed.locations == (
            Location(self._roots[0], "notes.md"),
            Location(self._roots[1], "a.md"),
        )

    def test_rejects_a_root_outside_the_given_set(self) -> None:
        with pytest.raises(ValueError, match="No accessible workspace"):
            route(Changeset((Delete("@other/notes.md"),)), self._roots)


class TestConversationTmp:
    """`/tmp` is the conversation's own folder, outside every workspace."""

    def test_each_conversation_resolves_only_its_own_folder(
        self, data_dir: Path
    ) -> None:
        for conversation in ("c1", "c2"):
            folder = tmp_dir(data_dir, owned(conversation))
            folder.mkdir(parents=True)
            (folder / "state.json").write_text(conversation)

        for conversation, other in (("c1", "c2"), ("c2", "c1")):
            paths = _deps(conversation).search_paths()
            sp, local, absolute = resolve_accessible_file(
                paths, "/tmp/state.json"
            ) or pytest.fail("unresolved")
            assert sp.mount.render(local) == "/tmp/state.json"
            assert absolute.read_text() == conversation
            assert (
                resolve_accessible_file(paths, f"/tmp/../{label_id(other)}/state.json")
                is None
            )

        # A caller without a conversation, such as MCP, has no `/tmp` at all.
        assert (
            resolve_accessible_file(_deps(None).search_paths(), "/tmp/state.json")
            is None
        )


class TestCaseFolding:
    """Paths compare the way the workspace's filesystem compares them."""

    @pytest.mark.usefixtures("folded_case")
    def test_a_filter_is_asked_about_the_name_on_disk(self, tmp_path: Path) -> None:
        """A hidden `secret.md` is not reachable as `SECRET.md` where case folds."""
        (tmp_path / "secret.md").write_text("x")
        hidden = DocumentFilter(excluded=frozenset({doc("secret.md")}))
        paths = (SearchPath.of(tmp_path, "~", implied=True, filter_func=hidden),)

        assert resolve_accessible_file(paths, "~/secret.md") is None
        assert resolve_accessible_file(paths, "~/SECRET.md") is None


class TestDocumentScopePrompt:
    """The selection the user makes with the eye toggle must reach the model.

    Nothing else ties a bare "in der Tabelle" to a file: the included half of
    the selection prunes no tool, so an unmentioned scope leaves the model with
    no idea which of the workspace's documents the question was about.
    """

    def _deps(
        self, included: list[FilterEntry], excluded: list[FilterEntry]
    ) -> UserDeps:
        user = User("u1")
        relevant, document_filters = parse_document_scope(included, excluded, user)
        return UserDeps(
            user=user,
            mode="interactive",
            document_filters=document_filters,
            relevant_documents=relevant,
        )

    def test_included_documents_are_named(self) -> None:
        scope = self._deps([doc("~/projekte/tabelle.md")], []).describe_document_scope()

        assert "~/projekte/tabelle.md" in scope
        assert "these documents" in scope

    def test_excluded_documents_are_named(self) -> None:
        scope = self._deps([], [doc("~/geheim.md")]).describe_document_scope()

        assert "~/geheim.md" in scope

    def test_no_selection_renders_nothing(self) -> None:
        """An unscoped chat must not spend prompt on an empty block."""
        assert self._deps([], []).describe_document_scope() == ""
