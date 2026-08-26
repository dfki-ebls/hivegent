"""Unit tests for the concrete WorkspaceScope grammar (~/@ convention)."""

import pytest

from hivegent.agents.common import UserDeps
from hivegent.server.common import parse_document_scope
from hivegent.store import Casebase, WorkspaceScope, scoped_operation


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


class TestScopedOperation:
    """Routing a canonical path to the store it names."""

    _stores = (Casebase.for_user("u"), Casebase.for_group("team"))

    @staticmethod
    async def _op(store: Casebase, local: str, suffix: str) -> str:
        return f"{store.store_key}:{local}:{suffix}"

    async def test_runs_against_each_addressed_store(self) -> None:
        run = scoped_operation(self._op, self._stores)

        assert await run("~/notes.md", "x") == "user:u:notes.md:x"
        assert await run("@team/notes.md", "x") == "group:team:notes.md:x"

    async def test_rejects_a_store_outside_the_given_set(self) -> None:
        run = scoped_operation(self._op, self._stores)

        with pytest.raises(ValueError, match="No accessible workspace"):
            await run("@other/notes.md", "x")


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
