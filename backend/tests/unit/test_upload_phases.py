"""Phased upload: progress and durable state never disagree.

The reserve/prepare/commit split must keep the invariant the product cares
about: whenever the UI is told an image description is being generated, the
markdown sidecar is actually stored — and if anything fails, the partial
entry is rolled back wholesale rather than left as an orphan.
"""

import io
from pathlib import Path
from typing import ClassVar

import PIL.Image
import PIL.PngImagePlugin
import pytest
from fastapi import HTTPException

from hivegent import workspace
from hivegent.chunkers.base import EntryMetadata
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.store import Casebase
from hivegent.types import LlmConfig
from hivegent.workspace import commit, prepare


class _Recorder:
    """Minimal :class:`~hivegent.types.ProgressReporter` capturing stages."""

    def __init__(self) -> None:
        self.stages: list[str] = []

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)

    def set_progress(self, current: int, total: int) -> None:
        pass


class _Chunked:
    chunks: ClassVar[tuple[()]] = ()
    pipeline: ClassVar[str] = "none"


def _png() -> bytes:
    buf = io.BytesIO()
    PIL.Image.new("RGB", (64, 48), (10, 80, 160)).save(buf, "PNG")
    return buf.getvalue()


def _png_with_metadata() -> bytes:
    """A PNG carrying an ancillary tEXt chunk that sanitisation would strip."""
    buf = io.BytesIO()
    info = PIL.PngImagePlugin.PngInfo()
    info.add_text("Comment", "x" * 256)
    PIL.Image.new("RGB", (64, 48), (10, 80, 160)).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


async def test_image_upload_reports_stage_and_stores_sidecar(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    async def fake_describe(
        filepath: str,
        content: bytes,
        media_type: str,
        contexts: list[str],
        llm: object,
    ) -> str:
        return "A vivid blue rectangle.\n"

    async def fake_chunk(*_args: object, **_kwargs: object) -> _Chunked:
        return _Chunked()

    monkeypatch.setattr(prepare, "_build_image_description", fake_describe)
    monkeypatch.setattr(commit, "chunk_and_index_document", fake_chunk)

    recorder = _Recorder()
    await workspace.upload(user_store, "photo.png", _png(), ctx=recorder)

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    assert "Generating image description" in recorder.stages
    assert (workspace_dir / "photo.png").exists()
    assert (workspace_dir / "photo.md").read_text() == "A vivid blue rectangle.\n"


async def test_failed_commit_leaves_no_orphan(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    async def fake_describe(*_args: object, **_kwargs: object) -> str:
        return "ignored\n"

    async def boom(*_args: object, **_kwargs: object) -> _Chunked:
        raise RuntimeError("indexing failed")

    async def no_metadata(*_args: object, **_kwargs: object) -> None:
        return None

    async def noop_delete(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(prepare, "_build_image_description", fake_describe)
    monkeypatch.setattr(commit, "chunk_and_index_document", boom)
    # The rollback resolves the entry from disk and drops its index rows; stub
    # the SQL touches so the test stays off any live database.
    monkeypatch.setattr(db_documents, "get_entry_metadata", no_metadata)
    monkeypatch.setattr(commit, "delete_chunked_document", noop_delete)

    with pytest.raises(RuntimeError):
        await workspace.upload(user_store, "photo.png", _png())

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    assert not (workspace_dir / "photo.png").exists()
    assert not (workspace_dir / "photo.md").exists()


async def test_image_upload_stores_original_verbatim(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    """The stored original keeps its ancillary chunks; sanitising is the model's job."""

    async def fake_describe(*_args: object, **_kwargs: object) -> str:
        return "caption\n"

    async def fake_chunk(*_args: object, **_kwargs: object) -> _Chunked:
        return _Chunked()

    monkeypatch.setattr(prepare, "_build_image_description", fake_describe)
    monkeypatch.setattr(commit, "chunk_and_index_document", fake_chunk)

    raw = _png_with_metadata()
    await workspace.upload(user_store, "photo.png", raw)

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    assert (workspace_dir / "photo.png").read_bytes() == raw


async def test_markdown_and_companion_original_land_as_one_entry(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    indexed_originals: list[str | None] = []

    async def fake_chunk(*_args: object, **kwargs: object) -> _Chunked:
        metadata = kwargs["entry_metadata"]
        assert isinstance(metadata, EntryMetadata)
        indexed_originals.append(metadata.original_path)
        return _Chunked()

    monkeypatch.setattr(commit, "chunk_and_index_document", fake_chunk)

    await workspace.upload(
        user_store,
        "report.md",
        b"# Report\n",
        original_path="report.pdf",
        original_content=b"%PDF",
    )

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    assert (workspace_dir / "report.md").read_text() == "# Report\n"
    assert (workspace_dir / "report.pdf").read_bytes() == b"%PDF"
    assert indexed_originals == ["report.pdf"]


async def test_replace_restores_entry_when_staged_install_fails(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    async def fake_describe(*_args: object, **_kwargs: object) -> str:
        return "old caption\n"

    async def fake_chunk(*_args: object, **_kwargs: object) -> _Chunked:
        return _Chunked()

    async def no_metadata(*_args: object, **_kwargs: object) -> None:
        return None

    async def noop_delete_subtree(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(prepare, "_build_image_description", fake_describe)
    monkeypatch.setattr(commit, "chunk_and_index_document", fake_chunk)
    await workspace.upload(user_store, "photo.png", b"old image")

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    assets = workspace_dir / "photo.assets"
    assets.mkdir()
    (assets / "old.png").write_bytes(b"old asset")
    monkeypatch.setattr(db_documents, "get_entry_metadata", no_metadata)
    monkeypatch.setattr(db_documents, "delete_subtree", noop_delete_subtree)

    real_replace = Path.replace
    failed = False

    def fail_new_original(self: Path, target: Path) -> Path:
        nonlocal failed
        if not failed and self.name == "photo.png" and self.parent.name == "new":
            failed = True
            raise OSError("simulated replacement failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_new_original)

    with pytest.raises(OSError, match="simulated replacement failure"):
        await workspace.replace_original(
            user_store, "photo.png", b"new image", new_filename="photo.png"
        )

    assert (workspace_dir / "photo.png").read_bytes() == b"old image"
    assert (workspace_dir / "photo.md").read_text() == "old caption\n"
    assert (assets / "old.png").read_bytes() == b"old asset"


async def test_upload_rejects_stem_already_in_flight(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    """A second upload of an in-flight stem is rejected, never raced to commit."""
    monkeypatch.setattr(workspace.locks, "_states", {})
    workspace.locks._add_inflight(user_store, "note.md")

    with pytest.raises(HTTPException) as exc:
        await workspace.upload(user_store, "note.md", b"# hi\n")

    assert exc.value.status_code == 409


async def test_destructive_ops_reject_while_stem_in_flight(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    """Every stem-touching mutation refuses to race a live phased upload.

    Covers the destructive ops and the in-place edits (rechunk/write/edit) that
    all route through the same locked-mutation gateway, so none can mutate or
    strip an entry whose upload has reserved but not committed it.
    """
    monkeypatch.setattr(workspace.locks, "_states", {})
    workspace.locks._add_inflight(user_store, "docs/note.md")

    for op in (
        workspace.delete_document(user_store, "docs/note.md"),
        workspace.delete_directory(user_store, "docs"),
        workspace.delete_all(user_store),
        workspace.move_document(user_store, user_store, "docs/note.md", "other"),
        workspace.move_directory(user_store, user_store, "docs", "other"),
        workspace.rechunk(user_store, "docs/note.md"),
        workspace.write_document_text(user_store, "docs/note.md", "x"),
        workspace.edit_document_text(user_store, "docs/note.md", "a", "b"),
        workspace.create_directory(user_store, "docs/note.md"),
        workspace.sync_entry_from_disk(user_store, "docs/note.md"),
        workspace.update_asset_description(
            user_store, "docs/note.md", "img1.png", "caption"
        ),
        workspace.generate_asset_description(
            user_store, "docs/note.md", "img1.png", LlmConfig()
        ),
        workspace.delete_asset_description(user_store, "docs/note.md", "img1.png"),
        # The upload owns the asset entries under its stem's `.assets` too, and
        # those are reachable only by their own stems — the claim has to cover
        # them or the index step could re-create rows for just-deleted files.
        workspace.delete_directory(user_store, "docs/note.assets"),
        workspace.delete_document(user_store, "docs/note.assets/img1.md"),
        workspace.move_directory(
            user_store, user_store, "docs/note.assets", "elsewhere"
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await op

        assert exc.value.status_code == 409


async def test_reprocess_failure_preserves_existing_entry(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase
) -> None:
    """A failed reconversion leaves the prior entry intact, never destroyed.

    The reserve/prepare/commit split keeps every workspace write in the final
    commit, so a failure (or cancel) during the lock-free prepare cannot strip
    the original or its description — the headline guarantee that makes a
    cancellable reconvert/replace safe.
    """

    async def fake_describe(*_args: object, **_kwargs: object) -> str:
        return "caption\n"

    async def fake_chunk(*_args: object, **_kwargs: object) -> _Chunked:
        return _Chunked()

    monkeypatch.setattr(prepare, "_build_image_description", fake_describe)
    monkeypatch.setattr(commit, "chunk_and_index_document", fake_chunk)
    await workspace.upload(user_store, "photo.png", _png())

    workspace_dir = user_store.workspace_dir(settings.data_dir)
    original = (workspace_dir / "photo.png").read_bytes()

    # No SQL row in this stateless test; reserve falls back to the on-disk entry.
    async def no_metadata(*_args: object, **_kwargs: object) -> None:
        return None

    async def boom_prepare(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(db_documents, "get_entry_metadata", no_metadata)
    monkeypatch.setattr(commit, "_prepare_upload", boom_prepare)

    with pytest.raises(RuntimeError):
        await workspace.replace_original(
            user_store, "photo.png", b"new bytes", new_filename="photo.png"
        )

    assert (workspace_dir / "photo.png").read_bytes() == original
    assert (workspace_dir / "photo.md").exists()
