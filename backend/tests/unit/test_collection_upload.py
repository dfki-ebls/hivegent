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
from hivegent.types import (
    CollectionCompleteEvent,
    CollectionProgressEvent,
    LlmConfig,
    PipelineSpec,
)
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
            member.parent.mkdir(parents=True, exist_ok=True)
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


def _failures(complete: CollectionCompleteEvent) -> dict[str, str]:
    """Map each failed member to its reason for concise assertions."""
    return {f.path: f.reason for f in complete.failed_files}


async def test_progress_seeds_zero_before_first_conversion(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(collections, "upload", ok_upload)

    archive = _make_zip(tmp_path, {"A.md": b"# a", "B.md": b"# b"})
    currents = [
        event.current
        async for event in collections.process_collection(
            user_store, archive, PipelineSpec(), LlmConfig()
        )
        if isinstance(event, CollectionProgressEvent)
    ]

    # A 0/total seed leads, so the tray has a live counter before the first
    # (potentially minutes-long) conversion completes, then one tick per file.
    assert currents == [0, 1, 2]


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

    assert _failures(complete) == {
        "M.md": collections._REASON_CONVERSION,
        "M.pdf": collections._REASON_OWNER_FAILED,
    }
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

    assert _failures(complete) == {"A.pdf": collections._REASON_NAME_CONFLICT}
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

    assert _failures(complete) == {"B.rtf": collections._REASON_NAME_CONFLICT}
    assert complete.markdown_files == 1
    # Exactly one original landed for the stem.
    assert (workspace_dir / "B.pdf").exists()
    assert not (workspace_dir / "B.rtf").exists()


async def test_markdown_owns_stem_when_original_sorts_before_md(
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

    # 'report.docx' sorts lexically before 'report.md', but the markdown must
    # still own the entry and the docx fold in as its companion original rather
    # than claiming the stem and getting re-converted from scratch.
    archive = _make_zip(tmp_path, {"report.docx": b"doc", "report.md": b"# body"})
    complete = await _run(user_store, archive)

    assert complete.failed_files == []
    assert complete.markdown_files == 1
    assert complete.converted_attachments == 1
    assert (workspace_dir / "report.docx").read_bytes() == b"doc"


async def test_companion_write_failure_keeps_committed_owner(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir(settings.data_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(
        _store: Casebase, safe: str, content: bytes, **_kwargs: object
    ) -> None:
        # Simulate a committed markdown entry landing on disk.
        (workspace_dir / safe).write_bytes(content)

    async def failing_sync(_store: Casebase, _reference: str) -> bool:
        raise RuntimeError("db hiccup during fold-in")

    monkeypatch.setattr(collections, "upload", ok_upload)
    monkeypatch.setattr(collections, "_sync_entry_from_disk_locked", failing_sync)

    archive = _make_zip(tmp_path, {"M.md": b"# body", "M.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert _failures(complete) == {"M.pdf": collections._REASON_WRITE_FAILED}
    # The companion failure removes only its own orphan original; the owning
    # markdown that already committed is never rolled back with it.
    assert (workspace_dir / "M.md").exists()
    assert not (workspace_dir / "M.pdf").exists()


async def test_os_junk_files_are_skipped_entirely(
    user_store: Casebase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded: list[str] = []

    async def record_upload(
        _store: Casebase, safe: str, _content: bytes, **_kwargs: object
    ) -> None:
        uploaded.append(safe)

    monkeypatch.setattr(collections, "upload", record_upload)

    # Finder/Explorer metadata and AppleDouble forks ride along with directory
    # uploads; they must neither convert nor count as failures.
    archive = _make_zip(
        tmp_path,
        {
            "doc.md": b"# body",
            ".DS_Store": b"\x00\x00\x00\x01Bud1",
            "__MACOSX/._doc.md": b"appledouble",
            "sub/Thumbs.db": b"thumbs",
        },
    )
    complete = await _run(user_store, archive)

    assert uploaded == ["doc.md"]
    assert complete.failed_files == []
    assert complete.markdown_files == 1
