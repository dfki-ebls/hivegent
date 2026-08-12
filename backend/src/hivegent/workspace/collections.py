"""ZIP collection import.

:func:`validate_collection_archive` enforces every size, count, and safety
limit up front, synchronously in the request path, so a violation is reported
to the caller immediately; :func:`process_collection` then trusts the archive
and feeds each file through the phased :func:`upload`.  The whole import holds a
store claim so a concurrent store-wide delete or directory move cannot
interleave between files and strip entries the collection already committed.
"""

import asyncio
import logging
import stat
import tempfile
import zipfile
import zlib
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Self

from fastapi import HTTPException

from ..concurrency import bounded_as_completed
from ..config import sanitize_document_path, settings
from ..converters.wikilinks import preprocess_markdown
from ..entries import (
    entry_exists,
    is_description_file,
    is_ignorable_path,
    stem_path_from_reference,
)
from ..humanize import format_bytes
from ..store import Casebase
from ..text import NOT_TEXT_REASON, read_text_file
from ..types import (
    CollectionCompleteEvent,
    CollectionProgressEvent,
    FailedFile,
    LlmConfig,
    PipelineSpec,
)
from .locks import _store_claim
from .uploads import upload

__all__ = [
    "process_collection",
    "validate_collection_archive",
]

logger = logging.getLogger(__name__)

# One short, user-facing reason per failure class, shown in the job tray and
# grouped there by reason.  Per-file exception detail is logged, not surfaced.
_REASON_EXISTS = "already in the workspace, delete to re-import"
_REASON_UNREADABLE_MD = "markdown could not be read"
_REASON_NAME_CONFLICT = "another file in this collection uses the same name"
_REASON_OWNER_FAILED = "the document it belongs to failed to import"
_REASON_CONVERSION = "conversion failed"


def _record_failure(
    path: str, reason: str, *, exc: BaseException | None = None
) -> FailedFile:
    """Log a skipped or failed member and return its record for the tray."""
    logger.warning("Skipping %s: %s", path, reason, exc_info=exc)

    return FailedFile(path=path, reason=reason)


def _validate_zip_entries(archive: zipfile.ZipFile) -> None:
    """Reject unsafe ZIP entries and limit violations.

    Catches symlinks, special files, traversal paths, too many files, and zip
    bombs (per-entry and cumulative uncompressed size).
    """
    file_count = 0
    total_uncompressed = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        file_count += 1
        if file_count > settings.limits.max_collection_files:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Collection has too many files ({file_count}). "
                    f"Maximum: {settings.limits.max_collection_files}"
                ),
            )

        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and not stat.S_ISREG(mode):
            raise HTTPException(
                status_code=400,
                detail=f"ZIP entry {info.filename!r} is not a regular file",
            )

        try:
            sanitize_document_path(info.filename)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"ZIP contains unsafe path {info.filename!r}: {exc}",
            ) from exc

        if info.file_size > settings.limits.max_file_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{info.filename}' in ZIP is too large "
                    f"({format_bytes(info.file_size)} decompressed). "
                    f"Maximum: {format_bytes(settings.limits.max_file_size_bytes)}"
                ),
            )
        total_uncompressed += info.file_size
        if total_uncompressed > settings.limits.max_collection_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Collection decompresses to more than "
                    f"{format_bytes(settings.limits.max_collection_size_bytes)}"
                ),
            )


def validate_collection_archive(archive_path: Path) -> None:
    """Reject a collection archive that is unreadable or violates a limit.

    The single place collection limits are enforced.  Called synchronously from
    the request path before the background job is submitted, so a too-large,
    too-many-files, or unsafe archive fails the request immediately with a clear
    reason instead of failing the job much later.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _validate_zip_entries(archive)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc


@dataclass(slots=True, frozen=True)
class _PlannedFile:
    """One archive member with its workspace identity, all resolved once."""

    relative_path: str
    safe: str
    stem: str
    is_markdown: bool

    @classmethod
    def from_relative(cls, relative_path: str, dest_dir: str = "") -> Self:
        """Resolve the safe destination path, logical stem, and kind of a member.

        ``relative_path`` stays the member's path within the archive (the read
        coordinate, shared with the wikilink resolver); ``dest_dir`` shifts only
        where it lands in the workspace, so a collection can drop into a subdir
        without disturbing its internal link resolution.
        """
        safe = sanitize_document_path(
            f"{dest_dir}/{relative_path}" if dest_dir else relative_path
        )
        return cls(
            relative_path=relative_path,
            safe=safe,
            stem=stem_path_from_reference(safe),
            is_markdown=is_description_file(safe),
        )


def _content_relative_paths(root: Path) -> list[str]:
    """Return every content file under *root* as a workspace-relative path.

    OS-generated metadata (``.DS_Store``, ``__MACOSX``, AppleDouble forks) rides
    along with directory uploads but is never user content, so it is dropped
    here rather than reaching the converter and failing as an unsupported binary.
    """
    paths: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not is_ignorable_path(rel):
            paths.append(rel)
    return paths


def _read_markdown(
    extract_root: Path, planned: _PlannedFile, collection_set: frozenset[str]
) -> bytes | None:
    """Read and wikilink-preprocess a markdown member, or ``None`` if unreadable."""
    try:
        decoded = read_text_file(extract_root / planned.relative_path)
    except OSError as exc:
        logger.warning(
            "Failed to read %s: %s", planned.relative_path, exc, exc_info=True
        )
        return None

    if decoded is None:
        logger.warning(
            "Skipping %s: content %s", planned.relative_path, NOT_TEXT_REASON
        )
        return None

    # Resolve wikilinks in the archive's own coordinates (``relative_path``,
    # matching ``collection_set``), not the workspace destination, so a subdir
    # drop never shifts a link out from under its target.
    return preprocess_markdown(
        decoded.text, planned.relative_path, collection_set
    ).content.encode("utf-8")


@dataclass(slots=True, frozen=True)
class _PlannedUpload:
    """One logical entry and any bytes already prepared during planning."""

    entry: _PlannedFile
    content: bytes | None = None
    original: _PlannedFile | None = None

    @property
    def member_count(self) -> int:
        """Return how many archive members this upload accounts for."""
        return 1 + (self.original is not None)


@dataclass(slots=True, frozen=True)
class _CollectionPlan:
    """Every archive member sorted into uploads or recorded failures.

    Uploads own independent stems and can commit concurrently.  A markdown and
    its companion original share one upload so they reserve, land, and index as
    one logical entry.  Each markdown upload carries its preprocessed bytes so
    commit never re-reads it.
    """

    uploads: tuple[_PlannedUpload, ...]
    dropped: tuple[FailedFile, ...]

    @property
    def total(self) -> int:
        """The number of archive members the plan accounts for."""
        return sum(upload.member_count for upload in self.uploads) + len(self.dropped)


async def _plan_collection(
    workspace_dir: Path,
    extract_root: Path,
    relative_paths: Sequence[str],
    dest_dir: str,
) -> _CollectionPlan:
    """Resolve every archive member's workspace identity and its role.

    A stem's markdown owns the logical entry and one sibling non-markdown folds
    in as its companion original; a lone non-markdown becomes a standalone
    attachment.  Members are sorted markdown-first within each stem, so the roles
    never depend on the archive's own file order (a ``.docx`` original sorts
    before its ``.md`` description, but must not claim the entry).
    """
    collection_set = frozenset(relative_paths)
    planned = sorted(
        (_PlannedFile.from_relative(rp, dest_dir) for rp in relative_paths),
        key=lambda p: (p.stem, not p.is_markdown, p.relative_path),
    )

    uploads: list[_PlannedUpload] = []
    dropped: list[FailedFile] = []

    def drop(planned_file: _PlannedFile, reason: str) -> None:
        dropped.append(_record_failure(planned_file.relative_path, reason))

    for _, grouped in groupby(planned, key=lambda p: p.stem):
        members = list(grouped)
        if entry_exists(workspace_dir, members[0].safe):
            for member in members:
                drop(member, _REASON_EXISTS)
            continue

        markdown: tuple[_PlannedFile, bytes] | None = None
        originals: list[_PlannedFile] = []
        for member in members:
            if not member.is_markdown:
                originals.append(member)
                continue
            if markdown is not None:
                drop(member, _REASON_NAME_CONFLICT)
                continue
            content = await asyncio.to_thread(
                _read_markdown, extract_root, member, collection_set
            )
            if content is None:
                drop(member, _REASON_UNREADABLE_MD)
                continue
            markdown = member, content

        if markdown is not None:
            markdown_file, content = markdown
            uploads.append(
                _PlannedUpload(
                    entry=markdown_file,
                    content=content,
                    original=originals[0] if originals else None,
                )
            )
            extras = originals[1:]
        elif originals:
            uploads.append(_PlannedUpload(entry=originals[0]))
            extras = originals[1:]
        else:
            extras = []

        for extra in extras:
            drop(extra, _REASON_NAME_CONFLICT)

    return _CollectionPlan(uploads=tuple(uploads), dropped=tuple(dropped))


async def process_collection(
    store: Casebase,
    archive_path: Path,
    spec: PipelineSpec,
    llm: LlmConfig,
    dest_dir: str = "",
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent]:
    """Process a ZIP collection, yielding per-file progress events.

    Re-validates the archive against the limits before extracting — the same
    cheap central-directory scan the request path runs up front — so extraction
    is never reached for an unsafe archive, even when called directly.  Each
    file then flows through the phased :func:`upload`, which holds the casebase
    lock only for its brief reserve and commit — so the rest of the workspace
    stays responsive while a large collection processes, and a cancel mid-run
    rolls back only the in-flight file while earlier files survive.  The whole
    import holds a store claim (:func:`_store_claim`) so a concurrent store-wide
    delete or directory move cannot interleave between files and strip entries
    the collection already committed.

    :func:`_plan_collection` fixes every member's role up front.  A markdown and
    its companion original flow through one upload, so neither can land without
    the other.
    """
    validate_collection_archive(archive_path)

    markdown_count = 0
    converted_count = 0
    failed: list[FailedFile] = []

    with _store_claim(store), tempfile.TemporaryDirectory() as tmp_dir:
        extract_root = Path(tmp_dir)

        try:
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_root)
        except (zipfile.BadZipFile, zlib.error) as exc:
            raise HTTPException(
                status_code=400, detail=f"Failed to extract ZIP: {exc!s}"
            ) from exc

        # Drop OS junk before the unwrap so a stray metadata file beside the real
        # content still collapses a single top-level directory.
        top_items = [
            item for item in extract_root.iterdir() if not is_ignorable_path(item.name)
        ]
        if len(top_items) == 1 and top_items[0].is_dir():
            extract_root = top_items[0]

        plan = await _plan_collection(
            store.workspace_dir(settings.data_dir),
            extract_root,
            _content_relative_paths(extract_root),
            dest_dir,
        )
        failed.extend(plan.dropped)

        def _progress(current: int) -> CollectionProgressEvent:
            return CollectionProgressEvent(current=current, total=plan.total)

        completed = len(failed)

        async def _run_upload(
            planned: _PlannedUpload,
        ) -> tuple[_PlannedUpload, BaseException | None]:
            # Failures are returned, not raised, so one bad file never aborts the
            # batch; a cancel still propagates so the phased upload rolls back.
            try:
                p = planned.entry
                content_bytes = planned.content
                if content_bytes is None:
                    content_bytes = await asyncio.to_thread(
                        (extract_root / p.relative_path).read_bytes
                    )
                original_content = (
                    await asyncio.to_thread(
                        (extract_root / planned.original.relative_path).read_bytes
                    )
                    if planned.original is not None
                    else None
                )
                await upload(
                    store,
                    p.safe,
                    content_bytes,
                    spec=spec,
                    llm=llm,
                    origin="collection",
                    original_path=(
                        planned.original.safe if planned.original is not None else None
                    ),
                    original_content=original_content,
                )
            except Exception as exc:  # noqa: BLE001
                return planned, exc
            return planned, None

        # Seed the counter (drops already included) before the first conversion
        # so the tray shows a live counter from the start; a slow first file
        # (docling can take minutes) would otherwise leave the job on a bare
        # "Processing" spinner with nothing telling the user work is underway.
        yield _progress(completed)

        # Logical entries run concurrently up to the collection cap.  Markdown
        # companions are already folded into their owner's upload.
        limit = settings.jobs.collection_concurrency
        async for planned, error in bounded_as_completed(
            plan.uploads, _run_upload, limit=limit
        ):
            p = planned.entry
            if error is None:
                if p.is_markdown:
                    markdown_count += 1
                else:
                    converted_count += 1
                if planned.original is not None:
                    converted_count += 1
            else:
                failed.append(
                    _record_failure(p.relative_path, _REASON_CONVERSION, exc=error)
                )
                if planned.original is not None:
                    failed.append(
                        _record_failure(
                            planned.original.relative_path, _REASON_OWNER_FAILED
                        )
                    )
            completed += planned.member_count
            yield _progress(completed)

    yield CollectionCompleteEvent(
        markdown_files=markdown_count,
        converted_attachments=converted_count,
        failed_files=failed,
        message=(
            f"Collection uploaded: {markdown_count} markdown, "
            f"{converted_count} processed attachments"
            + (f", {len(failed)} failed" if failed else "")
        ),
    )
