"""Unit tests for logical stem-entry path derivation and its SQL projection.

The repository never stores ``original_path`` directly: it reduces it to
``Document.original_suffix`` on write and reconstructs the path on read. For an
original whose pathlib suffix is empty — an extension-less upload (``abc``) or a
dotfile (``.env``) — the old encoding collapsed that to ``NULL`` and lost the
original, which silently broke delete, move, reconvert, and replace_original
(all of which branch on ``metadata.original_path``). These tests pin the full
round-trip so the projection cannot regress.
"""

import pytest

from hivegent.chunkers.base import EntryMetadata
from hivegent.db.documents import _EntryColumns, _entry_from_row, _original_suffix
from hivegent.db.models import Document
from hivegent.entries import (
    description_path_for_stem,
    original_path_for_stem,
    stem_path_from_reference,
)


def _project(entry: EntryMetadata) -> EntryMetadata:
    """Round-trip *entry* through the repository's column projection.

    Mirrors production exactly: ``_EntryColumns.from_entry`` reduces the entry
    to its stored columns, those populate an in-memory row, and
    ``_entry_from_row`` reconstructs the metadata — the same lossy seam the bug
    lived in.
    """
    columns = _EntryColumns.from_entry(entry)
    row = Document(stem_path=entry.stem_path, **columns.as_values())
    return _entry_from_row(row)


def _converted_entry(filepath: str) -> EntryMetadata:
    """An entry as a converting upload of *filepath* would build it."""
    stem = stem_path_from_reference(filepath)
    return EntryMetadata(
        entry_kind="convertible",
        stem_path=stem,
        description_path=description_path_for_stem(stem),
        original_path=filepath,
        assets_dir=None,
        mime=None,
        origin="upload",
        generated_by="converter",
        files=[description_path_for_stem(stem), filepath],
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
    """The original path reconstructs verbatim through the column projection."""
    assert stem_path_from_reference(filepath) == stem
    assert description_path_for_stem(stem) == description

    reconstructed = _project(_converted_entry(filepath))
    assert reconstructed.stem_path == stem
    assert reconstructed.description_path == description
    # The defect: empty-suffix originals came back as ``None`` here.
    assert reconstructed.original_path == filepath


def test_markdown_entry_has_no_original_after_projection() -> None:
    """A markdown-authored entry stays original-less end to end (not ``""``)."""
    entry = EntryMetadata(
        entry_kind="user_markdown",
        stem_path="notes/memo",
        description_path="notes/memo.md",
        original_path=None,
        assets_dir=None,
        mime=None,
        origin="upload",
        generated_by="user",
        files=["notes/memo.md"],
    )
    assert _EntryColumns.from_entry(entry).original_suffix is None
    assert _project(entry).original_path is None


def test_stored_suffix_distinguishes_absent_from_empty() -> None:
    """``None`` (no original) and ``""`` (extension-less original) stay distinct."""
    assert _original_suffix(None) is None
    assert _original_suffix("abc") == ""
    assert _original_suffix(".env") == ""
    assert _original_suffix("report.pdf") == ".pdf"

    assert original_path_for_stem("docs/note", None) is None
    assert original_path_for_stem("abc", "") == "abc"
    assert original_path_for_stem("docs/report", ".pdf") == "docs/report.pdf"
