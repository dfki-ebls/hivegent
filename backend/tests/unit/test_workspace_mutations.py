"""Unit tests for the canonical workspace text-mutation gateways.

These exercise the edit/write algorithm (occurrence counting, ``replace_all``,
write modes, error reporting) without a database by stubbing the re-indexing
step that ``_replace_text_locked`` shields.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException

from hivegent import workspace
from hivegent.config import settings
from hivegent.store import Casebase


@pytest.fixture()
def workspace_dir(
    user_store: Casebase, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Workspace root for *user_store* with re-indexing stubbed out."""

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(workspace, "chunk_and_index_document", _noop)
    path = user_store.workspace_dir(settings.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    yield path


class TestEditDocumentText:
    async def test_replaces_unique_match(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("hello world")
        result = await workspace.edit_document_text(user_store, "doc.md", "hello", "hi")
        assert "Replaced 1 occurrence" in result
        assert (workspace_dir / "doc.md").read_text() == "hi world"

    async def test_replace_all(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("foo foo foo")
        result = await workspace.edit_document_text(
            user_store, "doc.md", "foo", "bar", replace_all=True
        )
        assert "Replaced 3 occurrences" in result
        assert (workspace_dir / "doc.md").read_text() == "bar bar bar"

    async def test_missing_string_is_rejected(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("hello world")
        with pytest.raises(HTTPException) as exc:
            await workspace.edit_document_text(user_store, "doc.md", "absent", "x")
        assert exc.value.status_code == 400

    async def test_duplicate_without_replace_all_is_rejected(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("hi hi")
        with pytest.raises(HTTPException) as exc:
            await workspace.edit_document_text(user_store, "doc.md", "hi", "yo")
        assert "appears 2 times" in str(exc.value.detail)

    async def test_missing_file_is_404(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        with pytest.raises(HTTPException) as exc:
            await workspace.edit_document_text(user_store, "nope.md", "a", "b")
        assert exc.value.status_code == 404


class TestWriteDocumentText:
    async def test_replace_creates_file(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        result = await workspace.write_document_text(user_store, "new.md", "content")
        assert "Wrote" in result
        assert (workspace_dir / "new.md").read_text() == "content"

    async def test_append(self, user_store: Casebase, workspace_dir: Path) -> None:
        (workspace_dir / "doc.md").write_text("start")
        result = await workspace.write_document_text(
            user_store, "doc.md", " end", mode="append"
        )
        assert "Appended" in result
        assert (workspace_dir / "doc.md").read_text() == "start end"

    async def test_prepend(self, user_store: Casebase, workspace_dir: Path) -> None:
        (workspace_dir / "doc.md").write_text("end")
        result = await workspace.write_document_text(
            user_store, "doc.md", "start ", mode="prepend"
        )
        assert "Prepended" in result
        assert (workspace_dir / "doc.md").read_text() == "start end"

    async def test_append_to_missing_file_is_404(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(
                user_store, "nope.md", "x", mode="append"
            )
        assert exc.value.status_code == 404
