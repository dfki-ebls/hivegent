"""Unit tests for the document scope: what it hides and what it only names."""

from hivegent.agents.common import UserDeps
from hivegent.prompts import format_document_scope
from hivegent.server.common import parse_document_scope
from hivegent.store import Casebase
from hivegent.types import DocumentFilter


class TestDocumentFilter:
    """Tests for DocumentFilter.__call__."""

    def test_empty_filter_includes_everything(self) -> None:
        f = DocumentFilter()
        assert f("any/file.md")

    def test_exclude_covers_logical_entry_siblings(self) -> None:
        f = DocumentFilter(excluded=frozenset({"report.md"}))
        assert not f("report.md")
        assert not f("report.pdf")
        assert not f("report.assets/img.png")
        assert f("reportfoo.md")

    def test_directory_exclude_covers_itself_and_subtree(self) -> None:
        f = DocumentFilter(excluded=frozenset({"drafts/"}))
        assert f("report.md")
        assert not f("drafts")
        assert not f("drafts/wip.md")

    def test_root_entry_hides_the_whole_store(self) -> None:
        assert not DocumentFilter(excluded=frozenset({"/"}))("any/file.md")

    def test_scratch_is_never_hidden(self) -> None:
        """A chat selection must not strand a run halfway through its state."""
        f = DocumentFilter(excluded=frozenset({"/"}))
        assert f(".scratch/state.json")
        assert f("notes/.scratch/run.py")


class TestParseDocumentScope:
    """Tests for parse_document_scope."""

    def test_no_entries_yields_no_selection(self) -> None:
        relevant, user_filter, group_filters = parse_document_scope([], [], frozenset())
        assert relevant == frozenset()
        assert user_filter is None
        assert group_filters == {}

    def test_exclude_only_filters_the_store_it_names(self) -> None:
        _relevant, user_filter, group_filters = parse_document_scope(
            [], ["~/drafts/"], frozenset({"team"})
        )
        assert user_filter is not None
        assert not user_filter("drafts/wip.md")
        assert user_filter("report.md")
        assert group_filters == {}

    def test_include_restricts_nothing_and_comes_back_canonical(self) -> None:
        relevant, user_filter, group_filters = parse_document_scope(
            ["~/docs/", "@team/notes.md"], [], frozenset({"team"})
        )
        assert relevant == frozenset({"~/docs/", "@team/notes.md"})
        assert user_filter is None
        assert group_filters == {}

    def test_bare_scope_root_renders_as_the_workspace_root(self) -> None:
        relevant, _user_filter, _group_filters = parse_document_scope(
            ["~"], [], frozenset()
        )
        assert relevant == frozenset({"~/"})

    def test_unaddressable_entries_are_skipped(self) -> None:
        relevant, user_filter, group_filters = parse_document_scope(
            ["@stranger/docs/", "no-prefix.md"], ["@stranger/secret.md"], frozenset()
        )
        assert relevant == frozenset()
        assert user_filter is None
        assert group_filters == {}


class TestFormatDocumentScope:
    """Tests for the prompt-block renderer."""

    def test_nothing_selected_returns_empty(self) -> None:
        assert format_document_scope(frozenset(), frozenset()) == ""

    def test_relevant_paths_are_named_as_a_hint(self) -> None:
        text = format_document_scope(frozenset({"~/a.md", "~/b.md"}), frozenset())
        assert "Most relevant:" in text
        assert "- ~/a.md" in text
        assert "- ~/b.md" in text
        assert "not a restriction" in text
        assert "Hidden from this conversation:" not in text

    def test_hidden_paths_are_named_as_unavailable(self) -> None:
        text = format_document_scope(frozenset(), frozenset({"~/secret.md"}))
        assert "Hidden from this conversation:" in text
        assert "- ~/secret.md" in text
        assert "Most relevant:" not in text

    def test_both_halves_render_together(self) -> None:
        text = format_document_scope(
            frozenset({"~/docs/"}), frozenset({"~/docs/secret.md"})
        )
        assert "Most relevant:" in text
        assert "Hidden from this conversation:" in text


class TestDescribeDocumentScope:
    """Tests for UserDeps rendering its live selection to canonical paths."""

    @staticmethod
    def _deps(
        relevant_documents: frozenset[str] = frozenset(),
        document_filter: DocumentFilter | None = None,
        group_filters: dict[str, DocumentFilter] | None = None,
    ) -> UserDeps:
        return UserDeps(
            user_id="u",
            store=Casebase.for_user("u"),
            mode="interactive",
            group_stores=(Casebase.for_group("team"),),
            document_filter=document_filter,
            group_filters=group_filters or {},
            relevant_documents=relevant_documents,
        )

    def test_no_selection_is_empty(self) -> None:
        assert self._deps().describe_document_scope() == ""

    def test_relevant_documents_pass_through(self) -> None:
        text = self._deps(
            relevant_documents=frozenset({"~/a/r.md", "@team/docs/"})
        ).describe_document_scope()
        assert "- ~/a/r.md" in text
        assert "- @team/docs/" in text

    def test_group_excludes_render_with_group_prefix(self) -> None:
        text = self._deps(
            group_filters={"team": DocumentFilter(excluded=frozenset({"old/"}))}
        ).describe_document_scope()
        assert "- @team/old/" in text

    def test_root_exclude_renders_as_workspace_root(self) -> None:
        text = self._deps(
            document_filter=DocumentFilter(excluded=frozenset({"/"}))
        ).describe_document_scope()
        assert "- ~/" in text

    def test_rendering_is_deterministic_across_calls(self) -> None:
        # Sorted output keeps the cached prompt prefix byte-identical between
        # turns when the selection is unchanged.
        deps = self._deps(relevant_documents=frozenset({"~/b.md", "~/a.md", "~/c.md"}))
        assert deps.describe_document_scope() == deps.describe_document_scope()
