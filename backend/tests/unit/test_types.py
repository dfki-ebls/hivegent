"""Unit tests for the document scope: what it hides and what it only names."""

from pathlib import Path

import pytest
from exar.memory.mounts import Mount

from hivegent.agents.common import UserDeps
from hivegent.prompts import format_document_scope
from hivegent.server.common import parse_document_scope
from hivegent.store import GroupScope, HostGrant, User, UserScope, WorkspaceScope
from hivegent.types import DocumentFilter, FilterEntry
from tests.helpers import doc, folder

USER = User("u", read_groups=frozenset({"team"}))

PATHS = (
    "any/file.md",
    "docs/x.md",
    "docs/y.md",
    "docs/deep/x.md",
    "docs/other.md",
    "notes/a.md",
    "notes/a.pdf",
    "notes/a.v2.pdf",
    "notes/a.assets/fig.png",
    "notes/a.csv.cbrkit.json",
    "notes/b.md",
    "100%_done/a.md",
)


def _hiding(*entries: FilterEntry) -> DocumentFilter:
    return DocumentFilter(excluded=frozenset(entries))


@pytest.mark.parametrize(
    ("filter_", "hidden"),
    [
        (DocumentFilter(), []),
        (_hiding(folder("")), list(PATHS)),
        (_hiding(folder("docs")), [p for p in PATHS if p.startswith("docs/")]),
        (
            _hiding(doc("notes/a.md")),
            [
                "notes/a.md",
                "notes/a.pdf",
                "notes/a.assets/fig.png",
                "notes/a.csv.cbrkit.json",
            ],
        ),
        (_hiding(doc("notes/a.v2.pdf")), ["notes/a.v2.pdf"]),
    ],
)
def test_filter_hides_a_document_with_its_siblings_and_a_folder_with_its_subtree(
    filter_: DocumentFilter, hidden: list[str]
) -> None:
    assert [path for path in PATHS if not filter_(path)] == hidden


@pytest.mark.parametrize(
    "filter_",
    [
        DocumentFilter(),
        _hiding(folder("")),
        _hiding(folder("docs"), folder("100%_done")),
        _hiding(doc("notes/a.md")),
        _hiding(doc("notes/a.v2.pdf")),
    ],
)
def test_globs_agree_with_the_predicate(
    filter_: DocumentFilter, tmp_path: Path
) -> None:
    """The `exclude` globs hide exactly what the filter hides, as the mount of a search path sees it."""
    for path in PATHS:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).touch()

    mount = Mount(source=tmp_path, exclude=filter_.globs(tmp_path))

    assert {p for p in PATHS if mount.excludes(p)} == {
        p for p in PATHS if not filter_(p)
    }


def test_globs_hide_the_parts_of_an_excluded_document_added_later(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").touch()
    (tmp_path / "notes" / "a.v2.md").touch()
    mount = Mount(source=tmp_path, exclude=_hiding(doc("notes/a.md")).globs(tmp_path))

    assert [
        mount.excludes(path)
        for path in ("notes/a.pdf", "notes/a.assets/fig.png", "notes/a.v2.md")
    ] == [True, True, False]


def test_parse_document_scope_filters_only_the_store_an_exclude_names() -> None:
    _, filters = parse_document_scope([], [folder("~/drafts")], USER)

    assert list(filters) == [UserScope("u")]
    assert not filters[UserScope("u")]("drafts/wip.md")
    assert filters[UserScope("u")]("report.md")


def test_parse_document_scope_names_includes_canonically_without_restricting() -> None:
    relevant, filters = parse_document_scope(
        [
            folder("~/docs"),
            doc("@team/notes.md"),
            doc("/srv/a.md"),
            folder("~"),
            folder("/srv"),
        ],
        [],
        USER,
    )

    assert relevant == frozenset(
        {"~/docs/", "@team/notes.md", "/srv/a.md", "~/", "/srv/"}
    )
    assert filters == {}


def test_parse_document_scope_skips_unaddressable_entries() -> None:
    assert parse_document_scope(
        [folder("@stranger/docs"), doc("no-prefix.md")],
        [doc("@stranger/secret.md")],
        USER,
    ) == (frozenset(), {})


@pytest.mark.parametrize(
    ("relevant", "hidden", "expected", "absent"),
    [
        ({}, frozenset(), [], []),
        (
            {"~/a.md": "", "~/lab.md": "query_table runs SQL over it"},
            frozenset(),
            [
                "Most relevant:",
                "- ~/a.md\n",
                "- ~/lab.md (query_table runs SQL over it)",
                "not a restriction",
            ],
            ["Hidden from this conversation:"],
        ),
        (
            {},
            frozenset({"~/secret.md"}),
            ["Hidden from this conversation:", "- ~/secret.md"],
            ["Most relevant:"],
        ),
    ],
)
def test_format_document_scope_names_hints_and_hidden_paths(
    relevant: dict[str, str],
    hidden: frozenset[str],
    expected: list[str],
    absent: list[str],
) -> None:
    text = format_document_scope(relevant, hidden, "en")

    assert (text == "") == (not expected)
    assert all(part in text for part in expected)
    assert not any(part in text for part in absent)


def _deps(
    relevant_documents: frozenset[str] = frozenset(),
    document_filters: dict[WorkspaceScope, DocumentFilter] | None = None,
) -> UserDeps:
    return UserDeps(
        user=User("u", write_groups=frozenset({"team"})),
        mode="interactive",
        document_filters=document_filters or {},
        relevant_documents=relevant_documents,
    )


def test_no_selection_describes_nothing() -> None:
    assert _deps().describe_document_scope() == ""


def test_relevant_documents_pass_through_sorted() -> None:
    """Sorted output keeps the cached prompt prefix byte-identical while the selection is unchanged."""
    text = _deps(
        relevant_documents=frozenset({"~/b.md", "@team/docs/", "~/a.md"})
    ).describe_document_scope()

    assert [line for line in text.splitlines() if line.startswith("- ")] == [
        "- @team/docs/",
        "- ~/a.md",
        "- ~/b.md",
    ]


def test_selected_projection_names_the_original_it_came_from(data_dir: Path) -> None:
    """A spreadsheet is selected under the markdown it was projected to, so the block tells the run which original query_table has."""
    workspace = HostGrant().workspace(UserScope("u")).workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "lab.md").write_text("| a |\n|---|\n")
    (workspace / "lab.xlsx").write_bytes(b"PK\x03\x04")
    (workspace / "notes.md").write_text("prose")

    text = _deps(
        relevant_documents=frozenset({"~/lab.md", "~/notes.md"})
    ).describe_document_scope()

    assert "- ~/lab.md (query_table runs SQL over it" in text
    assert "'~/lab.xlsx'" in text
    assert "- ~/notes.md\n" in text


@pytest.mark.parametrize(
    ("scope", "path", "line"),
    [(GroupScope("team"), "old", "- @team/old/"), (UserScope("u"), "", "- ~/")],
)
def test_excluded_folders_render_with_their_store_prefix(
    scope: WorkspaceScope, path: str, line: str
) -> None:
    text = _deps(
        document_filters={scope: DocumentFilter(excluded=frozenset({folder(path)}))}
    ).describe_document_scope()

    assert line in text
