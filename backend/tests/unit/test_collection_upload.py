"""Unit tests for the ZIP collection importer.

Stateless: the DB-touching upload is stubbed with ``monkeypatch``.
This exercises the importer's file and stem bookkeeping without a live database.
"""

import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hivegent.shared.zips import extended_timestamp
from hivegent.store import Workspace
from hivegent.tools.entries import Part
from hivegent.types import (
    CollectionCompleteEvent,
    CollectionProgressEvent,
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


async def _run(store: Workspace, archive: Path) -> CollectionCompleteEvent:
    complete: CollectionCompleteEvent | None = None
    async for event in collections.process_collection(store, archive):
        if isinstance(event, CollectionCompleteEvent):
            complete = event
    assert complete is not None
    return complete


def _failures(complete: CollectionCompleteEvent) -> dict[str, str]:
    """Map each failed member to its reason for concise assertions."""
    return {f.path: f.reason for f in complete.failed_files}


@pytest.fixture()
def uploaded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the path of every upload instead of running it."""
    paths: list[str] = []

    async def record_upload(
        _store: Workspace, safe: str, _content: bytes, **_kwargs: object
    ) -> None:
        paths.append(safe)

    monkeypatch.setattr(collections, "upload", record_upload)

    return paths


async def test_cbrkit_companions_stay_out(
    user_store: Workspace, tmp_path: Path, uploaded: list[str]
) -> None:
    sidecar, cache = (Part("cars", kind, ".csv").path for kind in ("sidecar", "cache"))
    archive = _make_zip(tmp_path, {"cars.csv": b"a\n1\n", sidecar: b"{}", cache: b""})

    complete = await _run(user_store, archive)

    assert (uploaded, complete.failed_files) == (["cars.csv"], [])


async def test_progress_seeds_zero_before_first_conversion(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(collections, "upload", ok_upload)

    archive = _make_zip(tmp_path, {"A.md": b"# a", "B.md": b"# b"})
    currents = [
        event.current
        async for event in collections.process_collection(
            user_store, archive
        )
        if isinstance(event, CollectionProgressEvent)
    ]

    # A 0/total seed leads, so the tray has a live counter before the first
    # (potentially minutes-long) conversion completes, then one tick per file.
    assert currents == [0, 1, 2]


async def test_companion_original_dropped_when_owning_markdown_fails(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def failing_upload(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(collections, "upload", failing_upload)

    archive = _make_zip(tmp_path, {"M.md": b"# body", "M.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert _failures(complete) == {
        "M.md": collections._REASON_CONVERSION.en,
        "M.pdf": collections._REASON_OWNER_FAILED.en,
    }
    # The companion original is never written, so its owner's failure leaves no
    # orphan file on disk with no SQL row.
    assert not (workspace_dir / "M.pdf").exists()


async def test_companion_original_written_when_owning_markdown_succeeds(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(
        _store: Workspace,
        safe: str,
        content: bytes,
        **kwargs: object,
    ) -> None:
        (workspace_dir / safe).write_bytes(content)
        match kwargs.get("companion"):
            case (str(path), bytes(data)):
                (workspace_dir / path).write_bytes(data)
            case other:
                raise AssertionError(other)

    monkeypatch.setattr(collections, "upload", ok_upload)

    archive = _make_zip(tmp_path, {"N.md": b"# body", "N.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert complete.failed_files == []
    assert (workspace_dir / "N.pdf").read_bytes() == b"%PDF-1.4"


async def test_second_non_markdown_for_a_stem_is_rejected(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_upload(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(collections, "upload", ok_upload)

    # An attachment already IS the stem's original, so a second non-markdown
    # file for the same stem cannot be folded in, it must fail, not orphan a
    # second original.
    archive = _make_zip(tmp_path, {"A.docx": b"doc", "A.pdf": b"%PDF-1.4"})
    complete = await _run(user_store, archive)

    assert _failures(complete) == {"A.pdf": collections._REASON_NAME_CONFLICT.en}
    assert complete.converted_attachments == 1
    assert complete.markdown_files == 0


async def test_markdown_adopts_one_original_and_rejects_the_rest(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(
        _store: Workspace, _safe: str, _content: bytes, **kwargs: object
    ) -> None:
        match kwargs.get("companion"):
            case (str(path), bytes(data)):
                (workspace_dir / path).write_bytes(data)
            case _:
                pass

    monkeypatch.setattr(collections, "upload", ok_upload)

    archive = _make_zip(
        tmp_path, {"B.md": b"# body", "B.pdf": b"%PDF-1.4", "B.rtf": b"{\\rtf1}"}
    )
    complete = await _run(user_store, archive)

    assert _failures(complete) == {"B.rtf": collections._REASON_NAME_CONFLICT.en}
    assert complete.markdown_files == 1
    # Exactly one original landed for the stem.
    assert (workspace_dir / "B.pdf").exists()
    assert not (workspace_dir / "B.rtf").exists()


async def test_markdown_owns_stem_when_original_sorts_before_md(
    user_store: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_dir = user_store.workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ok_upload(
        _store: Workspace, _safe: str, _content: bytes, **kwargs: object
    ) -> None:
        match kwargs.get("companion"):
            case (str(path), bytes(data)):
                (workspace_dir / path).write_bytes(data)
            case other:
                raise AssertionError(other)

    monkeypatch.setattr(collections, "upload", ok_upload)

    # 'report.docx' sorts lexically before 'report.md', but the markdown must
    # still own the entry and the docx fold in as its companion original rather
    # than claiming the stem and getting re-converted anew.
    archive = _make_zip(tmp_path, {"report.docx": b"doc", "report.md": b"# body"})
    complete = await _run(user_store, archive)

    assert complete.failed_files == []
    assert complete.markdown_files == 1
    assert complete.converted_attachments == 1
    assert (workspace_dir / "report.docx").read_bytes() == b"doc"


async def test_os_junk_files_are_skipped_entirely(
    user_store: Workspace, tmp_path: Path, uploaded: list[str]
) -> None:
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


async def test_top_level_directory_is_kept(
    user_store: Workspace, tmp_path: Path, uploaded: list[str]
) -> None:
    # A dropped directory lands as itself, not as its loose contents.
    archive = _make_zip(tmp_path, {"docs/a.md": b"# a"})
    await _run(user_store, archive)

    assert uploaded == ["docs/a.md"]


@pytest.mark.parametrize(
    ("dos_hour", "extra"),
    [
        (12, b""),
        # A DOS date in local time defers to the exact extended timestamp.
        (14, extended_timestamp(datetime(2020, 5, 17, 12, 30, tzinfo=UTC).timestamp())),
    ],
)
async def test_member_date_reaches_the_upload(
    user_store: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dos_hour: int,
    extra: bytes,
) -> None:
    dates: list[object] = []

    async def record_upload(*_args: object, **kwargs: object) -> None:
        dates.append(kwargs["modified_at"])

    monkeypatch.setattr(collections, "upload", record_upload)

    archive = tmp_path / "collection.zip"
    info = zipfile.ZipInfo("doc.md", date_time=(2020, 5, 17, dos_hour, 30, 0))
    info.extra = extra
    info.external_attr = (stat.S_IFREG | 0o644) << 16

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, b"# body")

    await _run(user_store, archive)

    assert dates == [datetime(2020, 5, 17, 12, 30, tzinfo=UTC)]
