"""Unit tests for the ZIP collection importer.

Stateless: the DB-touching upload is replaced by one that lands the files.
This exercises the importer's file and stem bookkeeping without a live database.
"""

import stat
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hivegent.metadata import EntryOrigin
from hivegent.shared.zips import extended_timestamp
from hivegent.store import Workspace
from hivegent.tools.entries import Part
from hivegent.types import CollectionCompleteEvent, CollectionProgressEvent
from hivegent.workspace import collections


def _make_zip(directory: Path, files: dict[str, bytes]) -> Path:
    """Zip real files, not ``writestr``, so each entry is a regular file the importer's symlink guard admits."""
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


async def _run(
    store: Workspace, archive: Path, landing: Landing
) -> CollectionCompleteEvent:
    complete: CollectionCompleteEvent | None = None

    async for event in collections.process_collection(store, archive, upload=landing):
        if isinstance(event, CollectionCompleteEvent):
            complete = event

    assert complete is not None

    return complete


def _failures(complete: CollectionCompleteEvent) -> dict[str, str]:
    return {f.path: f.reason for f in complete.failed_files}


@dataclass(slots=True)
class Landing:
    """The upload that lands each file and its companion original in the workspace."""

    directory: Path
    paths: list[str] = field(default_factory=list[str])
    dates: list[datetime] = field(default_factory=list[datetime])
    fails: bool = False

    @property
    def files(self) -> set[str]:
        return {
            path.relative_to(self.directory).as_posix()
            for path in self.directory.rglob("*")
            if path.is_file()
        }

    async def __call__(
        self,
        store: Workspace,
        filepath: str,
        content: bytes,
        *,
        modified_at: datetime,
        origin: EntryOrigin,
        companion: tuple[str, bytes] | None,
    ) -> None:
        _ = store, origin

        if self.fails:
            raise RuntimeError("conversion failed")

        self.paths.append(filepath)
        self.dates.append(modified_at)
        (self.directory / filepath).parent.mkdir(parents=True, exist_ok=True)
        (self.directory / filepath).write_bytes(content)

        if companion is not None:
            path, data = companion
            (self.directory / path).write_bytes(data)


@pytest.fixture()
def landing(user_store: Workspace) -> Landing:
    kept = Landing(user_store.workspace_dir())
    kept.directory.mkdir(parents=True, exist_ok=True)

    return kept


async def test_cbrkit_companions_stay_out(
    user_store: Workspace, tmp_path: Path, landing: Landing
) -> None:
    sidecar, cache = (Part("cars", kind, ".csv").path for kind in ("sidecar", "cache"))
    archive = _make_zip(tmp_path, {"cars.csv": b"a\n1\n", sidecar: b"{}", cache: b""})

    complete = await _run(user_store, archive, landing)

    assert (landing.paths, complete.failed_files) == (["cars.csv"], [])


async def test_os_junk_is_skipped_and_a_top_level_directory_is_kept(
    user_store: Workspace, tmp_path: Path, landing: Landing
) -> None:
    """Finder and Explorer metadata ride along with directory uploads, they neither convert nor fail."""
    archive = _make_zip(
        tmp_path,
        {
            "docs/a.md": b"# body",
            ".DS_Store": b"\x00\x00\x00\x01Bud1",
            "__MACOSX/._a.md": b"appledouble",
            "sub/Thumbs.db": b"thumbs",
        },
    )

    complete = await _run(user_store, archive, landing)

    assert landing.paths == ["docs/a.md"]
    assert complete.failed_files == [] and complete.markdown_files == 1


async def test_progress_seeds_zero_before_the_first_conversion(
    user_store: Workspace, tmp_path: Path, landing: Landing
) -> None:
    """The tray has a live counter before the first, potentially minutes-long, conversion."""
    archive = _make_zip(tmp_path, {"A.md": b"# a", "B.md": b"# b"})

    currents = [
        event.current
        async for event in collections.process_collection(
            user_store, archive, upload=landing
        )
        if isinstance(event, CollectionProgressEvent)
    ]

    assert currents == [0, 1, 2]


async def test_companion_original_is_dropped_when_its_owning_markdown_fails(
    user_store: Workspace, tmp_path: Path, landing: Landing
) -> None:
    """No orphan original is left on disk with no SQL row."""
    landing.fails = True
    archive = _make_zip(tmp_path, {"M.md": b"# body", "M.pdf": b"%PDF-1.4"})

    complete = await _run(user_store, archive, landing)

    assert _failures(complete) == {
        "M.md": collections._REASON_CONVERSION.en,
        "M.pdf": collections._REASON_OWNER_FAILED.en,
    }
    assert landing.files == set()


@pytest.mark.parametrize(
    ("members", "failed", "markdown", "attachments", "landed"),
    [
        (["N.md", "N.pdf"], [], 1, 1, {"N.md", "N.pdf"}),
        # The markdown owns the entry even where its original sorts first.
        (["report.docx", "report.md"], [], 1, 1, {"report.md", "report.docx"}),
        # An entry has one original, so a second one cannot be folded in.
        (["B.md", "B.pdf", "B.rtf"], ["B.rtf"], 1, 1, {"B.md", "B.pdf"}),
        (["A.docx", "A.pdf"], ["A.pdf"], 0, 1, {"A.docx"}),
    ],
)
async def test_a_stem_has_one_original_which_a_markdown_adopts(
    user_store: Workspace,
    tmp_path: Path,
    landing: Landing,
    members: list[str],
    failed: list[str],
    markdown: int,
    attachments: int,
    landed: set[str],
) -> None:
    archive = _make_zip(tmp_path, {name: b"x" for name in members})

    complete = await _run(user_store, archive, landing)

    assert _failures(complete) == dict.fromkeys(
        failed, collections._REASON_NAME_CONFLICT.en
    )
    assert (complete.markdown_files, complete.converted_attachments) == (
        markdown,
        attachments,
    )
    assert landing.files == landed


@pytest.mark.parametrize(
    ("dos_hour", "extra"),
    [
        (12, b""),
        # A DOS date in local time defers to the exact extended timestamp.
        (14, extended_timestamp(datetime(2020, 5, 17, 12, 30, tzinfo=UTC).timestamp())),
    ],
)
async def test_member_date_reaches_the_upload(
    user_store: Workspace, tmp_path: Path, landing: Landing, dos_hour: int, extra: bytes
) -> None:
    archive = tmp_path / "collection.zip"
    info = zipfile.ZipInfo("doc.md", date_time=(2020, 5, 17, dos_hour, 30, 0))
    info.extra = extra
    info.external_attr = (stat.S_IFREG | 0o644) << 16

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, b"# body")

    _ = await _run(user_store, archive, landing)

    assert landing.dates == [datetime(2020, 5, 17, 12, 30, tzinfo=UTC)]
