"""Unit tests for logical stem-entry path derivation and its SQL projection.

An original whose pathlib suffix is empty (``abc`` or ``.env``) once collapsed to ``NULL`` in the stored columns and lost the original, so these pin the round-trip.
"""

import pytest
from cbrkit.io import COMPANION_SUFFIXES

from hivegent.db._common import stem_subtree_filter
from hivegent.db.documents import _entry_from_row, _EntryColumns, _original_suffix
from hivegent.db.models import Document
from hivegent.metadata import EntryMetadata
from hivegent.tools.entries import (
    FileKind,
    Part,
    is_below,
    rebase,
    stem_path_from_reference,
)


def _project(entry: EntryMetadata) -> EntryMetadata:
    """Round-trip *entry* through the repository's column projection."""
    columns = _EntryColumns.from_entry(entry)
    row = Document(stem_path=entry.stem_path, **columns.as_values())

    return _entry_from_row(row)


def _converted_entry(filepath: str) -> EntryMetadata:
    """An entry as a converting upload of *filepath* would build it."""
    stem = stem_path_from_reference(filepath)
    return EntryMetadata(
        entry_kind="convertible",
        stem_path=stem,
        description_path=Part(stem, "description").path,
        original_path=filepath,
        assets_dir=None,
        origin="upload",
        generated_by="converter",
    )


@pytest.mark.parametrize(
    ("filepath", "stem", "description"),
    [
        ("abc.xyz", "abc", "abc.md"),
        ("abc", "abc", "abc.md"),
        (".xyz", ".xyz", ".xyz.md"),
        ("docs/report.pdf", "docs/report", "docs/report.md"),
        ("docs/.env", "docs/.env", "docs/.env.md"),
        ("a.tar.gz", "a.tar", "a.tar.md"),
    ],
)
def test_original_survives_sql_projection(
    filepath: str, stem: str, description: str
) -> None:
    assert stem_path_from_reference(filepath) == stem
    assert Part(stem, "description").path == description

    reconstructed = _project(_converted_entry(filepath))
    assert reconstructed.stem_path == stem
    assert reconstructed.description_path == description
    assert reconstructed.original_path == filepath


def test_an_entry_without_an_original_stays_so_after_projection() -> None:
    """``None`` (no original) stays distinct from ``""`` (an extension-less one)."""
    entry = EntryMetadata(
        entry_kind="user_markdown",
        stem_path="notes/memo",
        description_path="notes/memo.md",
        original_path=None,
        assets_dir=None,
        origin="upload",
        generated_by="user",
    )

    assert _EntryColumns.from_entry(entry).original_suffix is None
    assert _project(entry).original_path is None
    assert [_original_suffix(path) for path in ("abc", ".env", "report.pdf")] == [
        "",
        "",
        ".pdf",
    ]


def test_subtree_filter_excludes_equal_stem_and_escapes_wildcards() -> None:
    """A stem equal to the prefix is the same-named sibling document (``notes.md`` beside ``notes/``), never swept along."""
    sql = str(
        stem_subtree_filter("100%_a").compile(compile_kwargs={"literal_binds": True})
    )
    assert "LIKE '100\\%\\_a/%'" in sql
    assert "=" not in sql


@pytest.mark.parametrize(
    "kind", ["description", "original", "assets", *COMPANION_SUFFIXES]
)
@pytest.mark.parametrize(
    ("stem", "suffix"), [("docs/cars", ".csv"), ("a.tar", ".gz"), (".env", "")]
)
def test_every_part_round_trips_through_its_subject(
    stem: str, suffix: str, kind: FileKind
) -> None:
    """A companion's path names its subject and kind, which name the same path again."""
    part = Part(stem, kind, "" if kind in ("description", "assets") else suffix)

    assert Part.of(part.path) == part


@pytest.mark.parametrize(
    ("path", "directory", "below"),
    [
        ("~/docs/a.md", "~/docs", True),
        ("~/docs-old/a.md", "~/docs", False),
        ("~/docs", "~/docs", False),
        ("@team/docs/a/b.md", "@team/docs", True),
    ],
)
def test_below_compares_segments_rather_than_prefixes(
    path: str, directory: str, below: bool
) -> None:
    """A sibling sharing a name prefix is never mistaken for a child."""
    assert is_below(path, directory) is below


def test_rebase_carries_a_path_between_trees() -> None:
    assert rebase("~/a/b/c.md", "~/a", "@team/x") == "@team/x/b/c.md"
    assert rebase("~/a", "~/a", "~/b") == "~/b"
    assert rebase("a/c.md", "a", "") == "c.md"
