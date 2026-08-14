"""Unit tests for the NFC path-normalization sweep and the near-miss hint.

Every sweep assertion here reads the directory *listing* rather than calling
``exists()``.  macOS resolves both spellings of a name to the same inode, so an
``exists()`` check passes before the sweep has done anything and would prove
nothing on a dev machine; the stored name is what actually changes, and APFS
preserves whatever spelling it was handed.

Escapes rather than literals throughout: this file is saved precomposed, so
spelling both forms out would compare NFC with NFC.
"""

import unicodedata
from pathlib import Path

import pytest

from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.store import Casebase
from hivegent.tools.base import near_miss_hint
from hivegent.workspace import normalize_workspace_paths

_NFD = "SU\u0308VOA.md"
_NFC = "S\u00dcVOA.md"


@pytest.fixture()
def workspace_dir(user_store: Casebase, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace root with the SQL half of the sweep stubbed out."""

    async def list_stem_paths(store: Casebase) -> list[str]:
        return []

    monkeypatch.setattr(db_documents, "list_stem_paths", list_stem_paths)
    return user_store.workspace_dir(settings.data_dir)


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


async def test_renames_decomposed_names_to_nfc(
    user_store: Casebase, workspace_dir: Path
) -> None:
    (workspace_dir / _NFD).write_text("body")
    # Pins the premise the assertion below rests on.  A filesystem that
    # normalizes on write fails here with a clear reason rather than turning
    # the real assertion into a tautology.
    assert _names(workspace_dir) == [_NFD]

    report = await normalize_workspace_paths(user_store)

    # A single entry also catches a sweep that copied instead of renaming.
    assert _names(workspace_dir) == [_NFC]
    assert report.files_renamed == 1
    assert report.collisions == 0

    # Idempotent: the endpoint advertises re-running as safe and cheap.
    assert (await normalize_workspace_paths(user_store)).files_renamed == 0


async def test_renames_nested_entries_deepest_first(
    user_store: Casebase, workspace_dir: Path
) -> None:
    # The directory and its child are both decomposed, so renaming the parent
    # first would invalidate the child's recorded path.
    nested = workspace_dir / "SU\u0308B"
    nested.mkdir()
    (nested / _NFD).write_text("body")

    await normalize_workspace_paths(user_store)

    assert _names(workspace_dir) == ["S\u00dcB"]
    assert _names(workspace_dir / "S\u00dcB") == [_NFC]


async def test_keeps_both_spellings_when_the_target_is_taken(
    user_store: Casebase, workspace_dir: Path
) -> None:
    (workspace_dir / _NFD).write_text("decomposed")
    (workspace_dir / _NFC).write_text("precomposed")
    if len(_names(workspace_dir)) == 1:
        pytest.skip("filesystem is normalization-insensitive: the spellings alias")

    report = await normalize_workspace_paths(user_store)

    # The one branch that could destroy data: a genuine second file is never
    # overwritten, only reported.
    assert (workspace_dir / _NFD).read_text() == "decomposed"
    assert (workspace_dir / _NFC).read_text() == "precomposed"
    assert report.files_renamed == 0
    assert report.collisions == 1


async def test_repoints_asset_references_of_a_renamed_stem(
    user_store: Casebase, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stem_nfd = "SU\u0308VOA"
    stem_nfc = "S\u00dcVOA"
    (workspace_dir / _NFD).write_text(f"![fig]({stem_nfd}.assets/fig.png)")
    (workspace_dir / f"{stem_nfd}.assets").mkdir()
    (workspace_dir / f"{stem_nfd}.assets/fig.png").write_bytes(b"\x89PNG")

    moved: list[tuple[str, str]] = []

    async def list_stem_paths(store: Casebase) -> list[str]:
        return [stem_nfd]

    async def move_document(
        src_store: Casebase, src_stem: str, dst_store: Casebase, dst_stem: str
    ) -> bool:
        moved.append((src_stem, dst_stem))
        return True

    monkeypatch.setattr(db_documents, "list_stem_paths", list_stem_paths)
    monkeypatch.setattr(db_documents, "move_document", move_document)

    await normalize_workspace_paths(user_store)

    assert moved == [(stem_nfd, stem_nfc)]
    body = (workspace_dir / _NFC).read_text()
    # The body addresses the payload by basename, so a stem whose spelling
    # changed leaves every image pointing at a directory that is gone.
    assert f"{stem_nfc}.assets/fig.png" in body
    assert unicodedata.is_normalized("NFC", body)


class TestNearMissHint:
    """Tests for the refusal hint that names an equivalent sibling."""

    def test_names_a_case_only_sibling(self, tmp_path: Path) -> None:
        # The bare "not found" a model got one step after seeing the file in a
        # listing was unactionable; naming the real spelling makes it fixable.
        (tmp_path / "Report.md").write_text("x")
        assert "Report.md" in near_miss_hint(tmp_path / "report.md")

    def test_empty_without_an_equivalent_sibling(self, tmp_path: Path) -> None:
        (tmp_path / "Report.md").write_text("x")
        assert near_miss_hint(tmp_path / "other.md") == ""

    def test_empty_when_the_parent_is_missing(self, tmp_path: Path) -> None:
        assert near_miss_hint(tmp_path / "gone" / "doc.md") == ""
