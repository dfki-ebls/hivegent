"""Unit tests for the ZIP collection importer.

Stateless: the DB-touching steps (`upload` and the companion sync) are stubbed
with ``monkeypatch`` so the importer's file/stem bookkeeping is exercised
without a live database.
"""

import zipfile
from pathlib import Path

import pytest

from hivegent.config import settings
from hivegent.store import Casebase
from hivegent.types import CollectionCompleteEvent, LlmConfig, PipelineSpec
from hivegent.workspace import collections


def _make_zip(directory: Path, files: dict[str, bytes]) -> Path:
    # Zip real files (not ``writestr``) so each entry carries a regular-file
    # mode and clears the importer's symlink/special-file guard.
    source = directory / "src"
    source.mkdir()
    archive = directory / "collection.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in files.items():
            member = source / name
            member.write_bytes(data)
            zf.write(member, name)
    return archive


async def _run(store: Casebase, archive: Path) -> CollectionCompleteEvent:
    complete: CollectionCompleteEvent | None = None
    async for event in collections.process_collection(
        store, archive, PipelineSpec(), LlmConfig()
    ):
        if isinstance(event, CollectionCompleteEvent):
            complete = event
    assert complete is not None
    return complete


async def test_companion_original_dropped_when_owning_markdown_fails(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir(settings.data_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def failing_upload(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("conversion failed")

    synced: list[str] = []

    async def record_sync(_store: Casebase, reference: str) -> bool:
        synced.append(reference)
        return False

    monkeypatch.setattr(collections, "upload", failing_upload)
    monkeypatch.setattr(collections, "_sync_entry_from_disk_locked", record_sync)

    archive = _make_zip(tmp_path, {"M.md": b"# body", "M.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert set(complete.failed_files) == {"M.md", "M.pdf"}
    # The companion original is never written, so its owner's failure leaves no
    # orphan file on disk with no SQL row.
    assert not (workspace_dir / "M.pdf").exists()
    assert synced == []


async def test_companion_original_written_when_owning_markdown_succeeds(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir(settings.data_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    synced: list[str] = []

    async def record_sync(_store: Casebase, reference: str) -> bool:
        synced.append(reference)
        return True

    monkeypatch.setattr(collections, "upload", ok_upload)
    monkeypatch.setattr(collections, "_sync_entry_from_disk_locked", record_sync)

    archive = _make_zip(tmp_path, {"N.md": b"# body", "N.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert complete.failed_files == []
    assert (workspace_dir / "N.pdf").read_bytes() == b"%PDF-1.4"
    assert synced == ["N.pdf"]


async def test_second_non_markdown_for_a_stem_is_rejected(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(collections, "upload", ok_upload)

    # An attachment already IS the stem's original, so a second non-markdown
    # file for the same stem cannot be folded in — it must fail, not orphan a
    # second original.
    archive = _make_zip(tmp_path, {"A.docx": b"doc", "A.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert complete.failed_files == ["A.pdf"]
    assert complete.converted_attachments == 1
    assert complete.markdown_files == 0


async def test_markdown_adopts_one_original_and_rejects_the_rest(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir(settings.data_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    async def record_sync(_store: Casebase, _reference: str) -> bool:
        return True

    monkeypatch.setattr(collections, "upload", ok_upload)
    monkeypatch.setattr(collections, "_sync_entry_from_disk_locked", record_sync)

    archive = _make_zip(
        tmp_path, {"B.md": b"# body", "B.pdf": b"%PDF-1.4", "B.rtf": b"{\\rtf1}"}
    )
    complete = await _run(user_store, archive)

    assert complete.failed_files == ["B.rtf"]
    assert complete.markdown_files == 1
    # Exactly one original landed for the stem.
    assert (workspace_dir / "B.pdf").exists()
    assert not (workspace_dir / "B.rtf").exists()
