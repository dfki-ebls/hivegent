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
from hivegent.chunkers.base import EntryMetadata
from hivegent.config import content_digest, content_hash, settings
from hivegent.db import documents as db_documents
from hivegent.db.documents import EntryState
from hivegent.entries import ContentStat
from hivegent.store import Casebase


@pytest.fixture()
def workspace_dir(
    user_store: Casebase, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Workspace root for *user_store* with re-indexing stubbed out."""

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", _noop)
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

    async def test_replace_all(self, user_store: Casebase, workspace_dir: Path) -> None:
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

    async def test_matching_expected_hash_succeeds(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("hello world")
        result = await workspace.edit_document_text(
            user_store,
            "doc.md",
            "hello",
            "hi",
            expected_hash=content_hash("hello world"),
        )
        assert "Replaced 1 occurrence" in result

    async def test_stale_expected_hash_is_409(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("hello world")
        with pytest.raises(HTTPException) as exc:
            await workspace.edit_document_text(
                user_store, "doc.md", "hello", "hi", expected_hash="stale0000000"
            )
        assert exc.value.status_code == 409


class TestWriteDocumentText:
    async def test_replace_creates_file(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        result = await workspace.write_document_text(user_store, "new.md", "content")
        assert "Wrote" in result
        assert (workspace_dir / "new.md").read_text() == "content"

    async def test_create_mode_rejects_existing_path(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        result = await workspace.write_document_text(
            user_store, "fresh.md", "body", mode="create"
        )
        assert "Created" in result

        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(
                user_store, "fresh.md", "other", mode="create"
            )
        assert exc.value.status_code == 409
        assert (workspace_dir / "fresh.md").read_text() == "body"

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

    async def test_stale_expected_hash_is_409(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("start")
        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(
                user_store,
                "doc.md",
                " end",
                mode="append",
                expected_hash="stale0000000",
            )
        assert exc.value.status_code == 409

    async def test_matching_expected_hash_succeeds(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "doc.md").write_text("start")
        result = await workspace.write_document_text(
            user_store,
            "doc.md",
            " end",
            mode="append",
            expected_hash=content_hash("start"),
        )
        assert "Appended" in result
        assert (workspace_dir / "doc.md").read_text() == "start end"

    async def test_expected_hash_on_missing_file_is_409(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """A hash for a file that does not exist signals a hallucinated read."""
        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(
                user_store, "new.md", "body", expected_hash="deadbeef0000"
            )
        assert exc.value.status_code == 409


def _entry_metadata(
    *,
    original_path: str | None = None,
    assets_dir: str | None = None,
) -> EntryMetadata:
    files = ["doc.md"]
    if original_path is not None:
        files.append(original_path)
    return EntryMetadata(
        entry_kind="user_markdown",
        stem_path="doc",
        description_path="doc.md",
        original_path=original_path,
        assets_dir=assets_dir,
        mime="text/markdown",
        origin="upload",
        generated_by="user",
        files=files,
    )


class TestSyncEntryFromDisk:
    async def test_refreshes_metadata_when_digest_matches_but_stat_moved(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (workspace_dir / "doc.md").write_text("body", encoding="utf-8")
        (workspace_dir / "doc.pdf").write_bytes(b"%PDF")
        (workspace_dir / "doc.assets").mkdir()
        updated: EntryMetadata | None = None

        async def get_entry_state(store: Casebase, reference: str) -> EntryState:
            _ = store, reference
            # Stale stat forces the read + hash; the digest then matches.
            return EntryState(
                content_digest=content_digest("body"),
                content_stat=ContentStat(mtime_ns=0, size=0),
                metadata=_entry_metadata(),
            )

        async def update_entry(
            store: Casebase, entry: EntryMetadata, stat: ContentStat | None
        ) -> bool:
            nonlocal updated
            _ = store, stat
            updated = entry
            return True

        async def chunk_and_index_document(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("unchanged markdown should not be indexed")

        monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
        monkeypatch.setattr(db_documents, "update_entry", update_entry)
        monkeypatch.setattr(
            workspace.indexing, "chunk_and_index_document", chunk_and_index_document
        )

        changed = await workspace.sync_entry_from_disk(user_store, "doc.md")

        assert changed is True
        assert updated is not None
        assert updated.original_path == "doc.pdf"
        assert updated.assets_dir == "doc.assets"
        assert updated.origin == "upload"

    async def test_fast_path_skips_unchanged_stat(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (workspace_dir / "doc.md").write_text("body", encoding="utf-8")
        stat = ContentStat.from_path(workspace_dir / "doc.md")

        async def get_entry_state(store: Casebase, reference: str) -> EntryState:
            _ = store, reference
            return EntryState(
                content_digest=content_digest("nonsense"),  # never read
                content_stat=stat,
                metadata=_entry_metadata(),
            )

        async def fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("matching stat must skip read, index, and write")

        monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
        monkeypatch.setattr(db_documents, "update_entry", fail)
        monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", fail)

        changed = await workspace.sync_entry_from_disk(user_store, "doc.md")

        assert changed is False


class TestDeleteAssetDescription:
    async def test_removes_companion_md_and_clears_entry(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        deleted: list[str] = []

        async def fake_delete(store: Casebase, reference: str) -> bool:
            _ = store
            deleted.append(reference)
            return True

        monkeypatch.setattr(workspace.indexing, "delete_chunked_document", fake_delete)
        assets = workspace_dir / "doc.assets"
        assets.mkdir()
        (assets / "img.png").write_bytes(b"binary")
        (assets / "img.md").write_text("a description", encoding="utf-8")

        entry = await workspace.delete_asset_description(
            user_store, "doc.md", "img.png"
        )

        assert not (assets / "img.md").exists()
        assert (assets / "img.png").exists()
        assert entry.description == ""
        assert entry.description_path is None
        assert deleted == ["doc.assets/img.md"]

    async def test_missing_asset_is_404(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fail(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("must not touch the index when the asset is absent")

        monkeypatch.setattr(workspace.indexing, "delete_chunked_document", fail)
        (workspace_dir / "doc.assets").mkdir()

        with pytest.raises(HTTPException) as exc:
            await workspace.delete_asset_description(user_store, "doc.md", "img.png")
        assert exc.value.status_code == 404
