"""Unit tests for the canonical workspace text-mutation gateways.

These exercise the edit/write algorithm (occurrence counting, ``replace_all``,
write modes, error reporting) without a database by stubbing the re-indexing
both persistence paths run: a description is indexed where it lies, while a
text original's rewrite lands through the phased commit that regenerates its
markdown projection.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import HTTPException

from hivegent import workspace
from hivegent.chunkers.base import EntryMetadata
from hivegent.config import content_digest, content_hash, settings
from hivegent.converters import VISION_MEDIA_TYPES
from hivegent.db import documents as db_documents
from hivegent.db.documents import EntryState
from hivegent.entries import ContentStat
from hivegent.server.operations import reads
from hivegent.store import Casebase
from hivegent.workspace import assets as workspace_assets
from hivegent.workspace import commit, documents


class _Chunked:
    """Stand-in for the indexed document the phased commit reports on."""

    chunks: ClassVar[tuple[()]] = ()
    pipeline: ClassVar[str] = "none"


@pytest.fixture()
def workspace_dir(
    user_store: Casebase, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Workspace root for *user_store* with every SQL touch stubbed out."""

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def _chunked(*_args: object, **_kwargs: object) -> _Chunked:
        return _Chunked()

    async def _no_rows(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(documents, "chunk_and_index_document", _noop)
    monkeypatch.setattr(commit, "chunk_and_index_document", _chunked)
    monkeypatch.setattr(db_documents, "get_entry_metadata", _noop)
    monkeypatch.setattr(db_documents, "delete_subtree", _no_rows)
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
        assert exc.value.status_code == 422

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


class TestCanonicalPathsInMessages:
    """Every message names the path a tool and a route take back, not the local one."""

    async def test_receipt_and_refusals_carry_the_scope_prefix(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        receipt = await workspace.write_document_text(user_store, "a/b.md", "hi")
        assert "'~/a/b.md'" in receipt

        with pytest.raises(HTTPException, match=r"'~/a/b\.md' changed since"):
            await workspace.write_document_text(
                user_store, "a/b.md", "x", expected_hash="0" * 12
            )

        (workspace_dir / "blocker.md").write_text("in the way")
        with pytest.raises(HTTPException, match=r"parent '~/blocker\.md' is a file"):
            await workspace.write_document_text(
                user_store, "blocker.md/child.md", "nope"
            )

    async def test_a_group_message_carries_the_group_prefix(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _noop(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(workspace.documents, "chunk_and_index_document", _noop)
        store = Casebase.for_group("team")
        store.workspace_dir(data_dir).mkdir(parents=True, exist_ok=True)

        receipt = await workspace.write_document_text(store, "notes.md", "hi")

        assert "'@team/notes.md'" in receipt


class TestWriteDocumentText:
    async def test_rejects_scratch_directory_as_a_file(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        with pytest.raises(HTTPException, match="scratch directory"):
            await workspace.write_document_text(user_store, ".scratch", "orphan")

        assert not (workspace_dir / ".scratch").exists()

    async def test_replace_creates_file(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        result = await workspace.write_document_text(user_store, "new.md", "content")
        assert "Wrote" in result
        assert (workspace_dir / "new.md").read_text() == "content"

    async def test_replace_creates_missing_parent_directories(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """A new document lands wherever its path says, folders and all."""
        result = await workspace.write_document_text(
            user_store, "reports/2026/q1.md", "content"
        )
        assert "Wrote" in result
        assert (workspace_dir / "reports" / "2026" / "q1.md").read_text() == "content"

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


class TestTextOriginals:
    """A text original is writable, and its projection follows the write.

    What may be written is decided by the bytes, exactly as on the read side;
    the name only decides whether the file *is* the indexed description or the
    original one is derived from.
    """

    async def test_edit_rewrites_original_and_its_projection(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "settings.ini").write_text("[db]\nhost = old\n")

        result = await workspace.edit_document_text(
            user_store, "settings.ini", "old", "new"
        )

        assert (workspace_dir / "settings.ini").read_text() == "[db]\nhost = new\n"
        assert "host = new" in (workspace_dir / "settings.md").read_text()
        assert "'~/settings.md' was regenerated" in result

    @pytest.mark.parametrize("encoding", ["cp1252", "utf-16"])
    async def test_edit_reports_legacy_encoding_transcode(
        self, user_store: Casebase, workspace_dir: Path, encoding: str
    ) -> None:
        original = workspace_dir / "settings.ini"
        original.write_bytes("city = Köln\n".encode(encoding))

        result = await workspace.edit_document_text(
            user_store, "settings.ini", "Köln", "Berlin"
        )

        assert original.read_bytes() == b"city = Berlin\n"
        assert f"transcoded from {encoding} to UTF-8" in result

    async def test_svg_is_created_as_the_markup_it_is(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """An SVG is text, so it is written and indexed rather than captioned."""
        await workspace.write_document_text(user_store, "diagram.svg", "<svg/>")

        assert (workspace_dir / "diagram.svg").read_text() == "<svg/>"
        assert "<svg/>" in (workspace_dir / "diagram.md").read_text()

    async def test_csv_is_created_and_projected(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """A converter claiming the format does not make it unwritable."""
        await workspace.write_document_text(user_store, "data/rows.csv", "a,b\n1,2\n")

        assert (workspace_dir / "data/rows.csv").read_text() == "a,b\n1,2\n"
        assert (workspace_dir / "data/rows.md").exists()

    async def test_binary_format_cannot_be_created_by_writing_text(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(user_store, "sheet.xlsx", "a,b")

        assert exc.value.status_code == 400
        assert not (workspace_dir / "sheet.xlsx").exists()

    async def test_write_creates_the_entry_and_its_projection(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        await workspace.write_document_text(user_store, "conf/app.xml", "<a/>")

        assert (workspace_dir / "conf/app.xml").read_text() == "<a/>"
        assert "<a/>" in (workspace_dir / "conf/app.md").read_text()

    async def test_binary_original_is_rejected(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / "report.pdf").write_bytes(b"%PDF\x00\x01binary")

        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(user_store, "report.pdf", "text")

        assert exc.value.status_code == 422
        assert (workspace_dir / "report.pdf").read_bytes().startswith(b"%PDF")

    @pytest.mark.parametrize("filename", sorted(VISION_MEDIA_TYPES))
    async def test_every_readable_binary_is_refused_by_name(
        self, user_store: Casebase, workspace_dir: Path, filename: str
    ) -> None:
        """The write side refuses exactly what ``read_document`` refuses.

        Both consult ``vision_media_type``, so a file is refused on its name
        even when its bytes happen to decode — otherwise growing that table
        would quietly make the two tools disagree, and the tool descriptions
        promising the model they agree would become false.
        """
        path = workspace_dir / f"notes{filename}"
        path.write_text("plain text wearing a binary extension")

        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(user_store, path.name, "replacement")

        assert exc.value.status_code == 422
        assert path.read_text() == "plain text wearing a binary extension"

    async def test_creating_a_converted_format_is_rejected(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """Only formats projected verbatim can be conjured out of text."""
        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(user_store, "report.docx", "text")

        assert exc.value.status_code == 400
        assert not (workspace_dir / "report.docx").exists()

    async def test_creating_an_original_cannot_supersede_an_entry(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """A new original claims the stem, so an occupied one is refused."""
        (workspace_dir / "notes.md").write_text("hand written")

        with pytest.raises(HTTPException) as exc:
            await workspace.write_document_text(user_store, "notes.ini", "x = 1")

        assert exc.value.status_code == 409
        assert (workspace_dir / "notes.md").read_text() == "hand written"
        assert not (workspace_dir / "notes.ini").exists()


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
        updated: list[EntryMetadata] = []

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
            _ = store, stat
            updated.append(entry)
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
        assert len(updated) == 1
        assert updated[0].original_path == "doc.pdf"
        assert updated[0].assets_dir == "doc.assets"
        assert updated[0].origin == "upload"

    async def test_derives_a_description_for_a_hand_dropped_text_file(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A config file dropped on disk becomes an entry, not invisible content."""
        (workspace_dir / "settings.ini").write_text("host = local\n", encoding="utf-8")
        indexed: list[tuple[str, EntryMetadata]] = []

        async def get_entry_state(store: Casebase, reference: str) -> None:
            _ = store, reference

        async def chunk_and_index_document(
            store: Casebase, filename: str, content: str, **kwargs: object
        ) -> None:
            _ = store, content
            metadata = kwargs["entry_metadata"]
            assert isinstance(metadata, EntryMetadata)
            indexed.append((filename, metadata))

        monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
        monkeypatch.setattr(
            workspace.indexing, "chunk_and_index_document", chunk_and_index_document
        )

        changed = await workspace.sync_entry_from_disk(user_store, "settings.ini")

        assert changed is True
        assert "host = local" in (workspace_dir / "settings.md").read_text()
        assert indexed[0][0] == "settings.md"
        assert indexed[0][1].original_path == "settings.ini"
        assert indexed[0][1].entry_kind == "convertible"

    async def test_refreshes_projection_after_original_is_hand_edited(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = workspace_dir / "settings.ini"
        description = workspace_dir / "settings.md"
        original.write_text("host = new\n", encoding="utf-8")
        description.write_text("```ini\nhost = old\n```\n", encoding="utf-8")
        old_digest = content_digest(description.read_text())
        old_stat = ContentStat.from_path(description)
        indexed: list[str] = []

        async def get_entry_state(store: Casebase, reference: str) -> EntryState:
            _ = store, reference
            return EntryState(
                content_digest=old_digest,
                content_stat=old_stat,
                metadata=_entry_metadata(original_path="settings.ini"),
            )

        async def chunk_and_index_document(
            store: Casebase, filename: str, content: str, **kwargs: object
        ) -> None:
            _ = store, filename, kwargs
            indexed.append(content)

        monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
        monkeypatch.setattr(
            workspace.indexing, "chunk_and_index_document", chunk_and_index_document
        )

        changed = await workspace.sync_entry_from_disk(user_store, "settings.md")

        assert changed is True
        assert "host = new" in description.read_text()
        assert "host = old" not in description.read_text()
        assert len(indexed) == 1
        assert "host = new" in indexed[0]

    async def test_leaves_a_hand_dropped_binary_inert(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Nothing can be projected from bytes that are not text."""
        (workspace_dir / "blob.bin").write_bytes(b"\x00\x01\x02")
        dropped: list[str] = []

        async def delete_chunked_document(store: Casebase, reference: str) -> bool:
            _ = store
            dropped.append(reference)
            return False

        async def fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("an unprojectable original must not be indexed")

        monkeypatch.setattr(
            workspace.indexing, "delete_chunked_document", delete_chunked_document
        )
        monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", fail)

        changed = await workspace.sync_entry_from_disk(user_store, "blob.bin")

        assert changed is False
        assert not (workspace_dir / "blob.md").exists()
        assert (workspace_dir / "blob.bin").exists()
        assert dropped == ["blob.md"]

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
    async def test_nested_asset_can_be_listed_and_updated(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fake_chunk(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(workspace_assets, "chunk_and_index_document", fake_chunk)
        images = workspace_dir / "doc.assets/images"
        images.mkdir(parents=True)
        (images / "img.png").write_bytes(b"binary")

        updated = await workspace.update_asset_description(
            user_store, "doc.md", "images/img.png", "nested description"
        )
        listed = reads.list_assets(user_store, "doc.md")

        assert updated.name == "images/img.png"
        assert updated.description_path == "doc.assets/images/img.md"
        assert [(asset.name, asset.description) for asset in listed.assets] == [
            ("images/img.png", "nested description")
        ]

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

        monkeypatch.setattr(workspace_assets, "delete_chunked_document", fake_delete)
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

        monkeypatch.setattr(workspace_assets, "delete_chunked_document", fail)
        (workspace_dir / "doc.assets").mkdir()

        with pytest.raises(HTTPException) as exc:
            await workspace.delete_asset_description(user_store, "doc.md", "img.png")
        assert exc.value.status_code == 404


class TestClearScratch:
    async def test_drops_nested_scratch_and_keeps_documents(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        (workspace_dir / ".scratch").mkdir()
        (workspace_dir / ".scratch" / "state.json").write_text("{}")
        (workspace_dir / "notes" / ".scratch").mkdir(parents=True)
        (workspace_dir / "notes" / ".scratch" / "run.py").write_text("print(1)")
        (workspace_dir / "notes" / "doc.md").write_text("content")

        assert await workspace.clear_scratch(user_store) == 2

        assert not (workspace_dir / ".scratch").exists()
        assert not (workspace_dir / "notes" / ".scratch").exists()
        assert (workspace_dir / "notes" / "doc.md").read_text() == "content"

    async def test_empty_workspace_clears_nothing(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        assert await workspace.clear_scratch(user_store) == 0
