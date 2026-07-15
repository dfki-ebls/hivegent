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
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from fastapi import HTTPException

from ..concurrency import bounded_as_completed, shield_to_completion
from ..config import sanitize_document_path, settings
from ..humanize import format_bytes
from ..converters.wikilinks import preprocess_markdown
from ..entries import (
    entry_exists,
    is_description_file,
    is_ignorable_path,
    stem_path_from_reference,
)
from ..store import Casebase
from ..types import (
    CollectionCompleteEvent,
    CollectionProgressEvent,
    FailedFile,
    LlmConfig,
    PipelineSpec,
)
from .indexing import _sync_entry_from_disk_locked
from .locks import _store_claim, store_lock
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
_REASON_WRITE_FAILED = "could not be written to the workspace"
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
class _Failed:
    """A planning-phase drop of a member, carrying its reason for the tray."""

    reason: str


# A planned member's disposition: one of the three commit kinds, or a drop that
# carries its own reason, so a failed role can never exist without one.
_Role = Literal["markdown", "attachment", "companion"] | _Failed


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
        rel = str(path.relative_to(root).as_posix())
        if not is_ignorable_path(rel):
            paths.append(rel)
    return paths


def _read_markdown(
    extract_root: Path, planned: _PlannedFile, collection_set: frozenset[str]
) -> bytes | None:
    """Read and wikilink-preprocess a markdown member, or ``None`` if unreadable."""
    try:
        text = (extract_root / planned.relative_path).read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "Failed to read %s: %s", planned.relative_path, exc, exc_info=True
        )
        return None

    # Resolve wikilinks in the archive's own coordinates (``relative_path``,
    # matching ``collection_set``), not the workspace destination, so a subdir
    # drop never shifts a link out from under its target.
    return preprocess_markdown(
        text, planned.relative_path, collection_set
    ).content.encode("utf-8")


async def _write_companion_original(
    store: Casebase, extract_root: Path, planned: _PlannedFile
) -> Literal["ok", "failed"]:
    """Write a companion original to disk and fold it into its owner's SQL row.

    The owning markdown committed earlier in the run; on any failure only the
    just-written original is removed, so the owner is never rolled back with it
    (a stem-keyed delete would take the description, its chunks, and assets too).
    The SQL sync runs to completion even on a cancel
    (:func:`shield_to_completion`), so a cancelled collection can never strand the
    original on disk without its owner's row linking it.
    """
    original_path = store.workspace_dir(settings.data_dir) / planned.safe
    try:
        async with store_lock(store):
            original_bytes = (extract_root / planned.relative_path).read_bytes()
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(original_bytes)
            # The owner sorts before its companion and is already indexed with no
            # original linked; fold the just-written original into its SQL row so
            # delete, move, and reconvert see it without waiting for a boot.
            await shield_to_completion(
                _sync_entry_from_disk_locked(store, planned.safe)
            )
    except Exception as exc:
        logger.warning(
            "Failed to write original %s: %s", planned.relative_path, exc, exc_info=True
        )
        original_path.unlink(missing_ok=True)
        return "failed"

    return "ok"


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

    A stem's markdown owns the logical entry and one sibling non-markdown folds
    in as its companion original; a lone non-markdown becomes a standalone
    attachment.  Planning sorts a stem's markdown ahead of its siblings, so the
    roles never depend on the archive's own file order (a ``.docx`` original
    sorts before its ``.md`` description, but must not claim the entry).
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

        relative_paths = _content_relative_paths(extract_root)
        collection_set = frozenset(relative_paths)
        workspace_dir = store.workspace_dir(settings.data_dir)

        # Markdown-first within each stem so a description claims its entry ahead
        # of any sibling original, whatever the archive's raw ordering.
        planned = sorted(
            (_PlannedFile.from_relative(rp, dest_dir) for rp in relative_paths),
            key=lambda p: (p.stem, not p.is_markdown, p.relative_path),
        )

        preprocessed_markdown: dict[str, bytes] = {}
        claimed_stems: set[str] = set()
        original_taken: set[str] = set()
        roles: dict[str, _Role] = {}

        for p in planned:
            if entry_exists(workspace_dir, p.safe):
                # A stem already backed by an on-disk entry is left untouched.
                roles[p.relative_path] = _Failed(_REASON_EXISTS)
            elif p.stem not in claimed_stems:
                # First file for the stem becomes its entry: a markdown is the
                # indexed description, a non-markdown a standalone attachment.
                claimed_stems.add(p.stem)
                if not p.is_markdown:
                    original_taken.add(p.stem)
                    roles[p.relative_path] = "attachment"
                elif (
                    md := _read_markdown(extract_root, p, collection_set)
                ) is not None:
                    preprocessed_markdown[p.safe] = md
                    roles[p.relative_path] = "markdown"
                else:
                    # Unreadable markdown: unclaim so a sibling original can still
                    # convert as its own attachment.
                    claimed_stems.discard(p.stem)
                    roles[p.relative_path] = _Failed(_REASON_UNREADABLE_MD)
            elif not p.is_markdown and p.stem not in original_taken:
                # The lone source original for a markdown entry this batch claimed.
                original_taken.add(p.stem)
                roles[p.relative_path] = "companion"
            else:
                # A duplicate markdown, or a second original for one stem.
                roles[p.relative_path] = _Failed(_REASON_NAME_CONFLICT)

        # Stems whose owning entry committed this run; a companion original is
        # only written once its owner is in here, so a failed owner never leaves
        # an orphaned original behind.
        committed_stems: set[str] = set()
        total = len(planned)

        def _progress(current: int) -> CollectionProgressEvent:
            return CollectionProgressEvent(current=current, total=total)

        # Planning already fixed every member's role, so split them once:
        # primaries (a markdown description or a standalone attachment) own
        # independent stems and convert/index concurrently; companions must wait
        # for their owner; planning-phase drops need no work and are recorded now.
        primaries: list[_PlannedFile] = []
        companions: list[_PlannedFile] = []
        for p in planned:
            role = roles[p.relative_path]
            if isinstance(role, _Failed):
                failed.append(_record_failure(p.relative_path, role.reason))
            elif role == "companion":
                companions.append(p)
            else:
                primaries.append(p)

        completed = len(failed)

        async def _run_primary(
            p: _PlannedFile,
        ) -> tuple[_PlannedFile, BaseException | None]:
            # Failures are returned, not raised, so one bad file never aborts the
            # batch; a cancel still propagates so the phased upload rolls back.
            try:
                if p.is_markdown:
                    content_bytes = preprocessed_markdown[p.safe]
                else:
                    content_bytes = await asyncio.to_thread(
                        (extract_root / p.relative_path).read_bytes
                    )
                await upload(
                    store, p.safe, content_bytes, spec=spec, llm=llm, origin="collection"
                )
            except Exception as exc:
                return p, exc
            return p, None

        async def _run_companion(p: _PlannedFile) -> tuple[_PlannedFile, str | None]:
            # The owner sorts first and settled in the primary phase; without its
            # committed entry there is nothing to fold this original into, and
            # writing it would strand a file with no SQL row (reconcile ingests
            # only markdown, never bare originals).
            if p.stem not in committed_stems:
                return p, _REASON_OWNER_FAILED
            if await _write_companion_original(store, extract_root, p) == "ok":
                return p, None
            return p, _REASON_WRITE_FAILED

        # Seed the counter (drops already included) before the first conversion
        # so the tray shows a live counter from the start; a slow first file
        # (docling can take minutes) would otherwise leave the job on a bare
        # "Processing" spinner with nothing telling the user work is underway.
        yield _progress(completed)

        # Primaries run concurrently up to the collection cap.  With the pool
        # active they convert in parallel; otherwise the win is overlapping one
        # file's embed/IO tail with the next file's conversion.
        limit = settings.jobs.collection_concurrency
        async for p, error in bounded_as_completed(primaries, _run_primary, limit=limit):
            if error is None:
                committed_stems.add(p.stem)
                # Count only on success, so the totals never overstate the import.
                if p.is_markdown:
                    markdown_count += 1
                else:
                    converted_count += 1
            else:
                failed.append(
                    _record_failure(p.relative_path, _REASON_CONVERSION, exc=error)
                )
            completed += 1
            yield _progress(completed)

        # Companions only run once every owner has settled, so they follow the
        # primary phase; each is a quick original write plus SQL fold-in.
        async for p, reason in bounded_as_completed(
            companions, _run_companion, limit=limit
        ):
            if reason is None:
                converted_count += 1
            else:
                failed.append(_record_failure(p.relative_path, reason))
            completed += 1
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
