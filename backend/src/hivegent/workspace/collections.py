"""ZIP collection import.

:func:`validate_collection_archive` enforces every size, count, and safety
limit up front, synchronously in the request path, so a violation is reported
to the caller immediately; :func:`process_collection` then trusts the archive
and feeds each file through the phased :func:`upload`.  The whole import holds a
store claim so a concurrent store-wide delete or directory move cannot
interleave between files and strip entries the collection already committed.
"""

import logging
import stat
import tempfile
import zipfile
import zlib
from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import HTTPException

from ..config import sanitize_document_path, settings
from ..converters.base import is_markdown_suffix
from ..converters.wikilinks import preprocess_markdown
from ..entries import entry_exists, stem_path_from_reference
from ..store import Casebase
from ..types import (
    CollectionCompleteEvent,
    CollectionProgressEvent,
    LlmConfig,
    PipelineSpec,
)
from .commit import _rollback_on_failure
from .indexing import _sync_entry_from_disk_locked
from .locks import _store_claim, store_lock
from .uploads import upload

__all__ = [
    "process_collection",
    "validate_collection_archive",
]

logger = logging.getLogger(__name__)


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
                status_code=400,
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
                status_code=400,
                detail=(
                    f"File '{info.filename}' in ZIP is too large "
                    f"({info.file_size} bytes decompressed). "
                    f"Maximum: {settings.limits.max_file_size_bytes} bytes"
                ),
            )
        total_uncompressed += info.file_size
        if total_uncompressed > settings.limits.max_collection_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection decompresses to more than "
                    f"{settings.limits.max_collection_size_bytes} bytes"
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


async def process_collection(
    store: Casebase,
    archive_path: Path,
    spec: PipelineSpec,
    llm: LlmConfig,
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
    """
    validate_collection_archive(archive_path)

    failed: list[str] = []
    failed_set: set[str] = set()
    markdown_count = 0
    converted_count = 0

    def _fail(path: str) -> None:
        failed.append(path)
        failed_set.add(path)

    with _store_claim(store), tempfile.TemporaryDirectory() as tmp_dir:
        extract_root = Path(tmp_dir)

        try:
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_root)
        except (zipfile.BadZipFile, zlib.error) as exc:
            raise HTTPException(
                status_code=400, detail=f"Failed to extract ZIP: {exc!s}"
            ) from exc

        top_items = list(extract_root.iterdir())
        if len(top_items) == 1 and top_items[0].is_dir():
            extract_root = top_items[0]

        collection_files = sorted(
            str(path.relative_to(extract_root).as_posix())
            for path in extract_root.rglob("*")
            if path.is_file()
        )
        collection_set = frozenset(collection_files)
        workspace_dir = store.workspace_dir(settings.data_dir)
        preprocessed_markdown: dict[str, bytes] = {}
        companion_originals: set[str] = set()
        # Stems an entry in this batch has claimed; the first file for a stem
        # claims it.
        claimed_stems: set[str] = set()
        # Stems that already own an original file, so any further non-markdown
        # for the stem is a duplicate — this is what keeps a stem from ending up
        # with two originals.
        original_taken: set[str] = set()

        for relative_path in collection_files:
            safe = sanitize_document_path(relative_path)
            stem = stem_path_from_reference(safe)
            is_markdown = is_markdown_suffix(PurePosixPath(safe).suffix.lower())

            # A stem already backed by an on-disk entry is left untouched.
            if entry_exists(workspace_dir, safe):
                _fail(relative_path)
                continue

            if stem not in claimed_stems:
                # First file for this stem becomes its entry; a non-markdown
                # entry (an attachment) is its own original.
                claimed_stems.add(stem)
                if not is_markdown:
                    original_taken.add(stem)
            elif not is_markdown and stem not in original_taken:
                # The lone source original for a markdown entry in this batch
                # (a non-markdown claimer already took ``original_taken``).
                companion_originals.add(relative_path)
                original_taken.add(stem)
                continue
            else:
                # A duplicate markdown, or a second original for one stem.
                _fail(relative_path)
                continue

            if is_markdown:
                try:
                    text = (extract_root / relative_path).read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", relative_path, exc)
                    _fail(relative_path)
                    # Unclaim the stem so a sibling original can still convert.
                    claimed_stems.discard(stem)
                    continue
                preprocessed_markdown[safe] = preprocess_markdown(
                    text, safe, collection_set
                ).content.encode("utf-8")

        # Stems whose owning entry committed this run; a companion original is
        # only written once its owner is in here, so a failed owner never leaves
        # an orphaned original behind.
        committed_stems: set[str] = set()
        total = len(collection_files)

        def _progress(
            path: str, current: int, status: Literal["ok", "failed"]
        ) -> CollectionProgressEvent:
            return CollectionProgressEvent(
                file=path, current=current, total=total, status=status
            )

        for current, relative_path in enumerate(collection_files, start=1):
            safe = sanitize_document_path(relative_path)
            stem = stem_path_from_reference(safe)
            if relative_path in failed_set:
                yield _progress(relative_path, current, "failed")
                continue

            if relative_path in companion_originals:
                # The owning markdown sorts first and has already been processed;
                # if its import failed there is no entry to fold this original
                # into, and writing it would strand a file with no SQL row
                # (reconcile ingests only markdown, never bare originals).
                if stem not in committed_stems:
                    _fail(relative_path)
                    yield _progress(relative_path, current, "failed")
                    continue
                try:
                    async with store_lock(store):
                        async with _rollback_on_failure(store, (safe,)):
                            original_bytes = (extract_root / relative_path).read_bytes()
                            original_path = workspace_dir / safe
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            original_path.write_bytes(original_bytes)
                            # The owning markdown sorts before its companion and
                            # so is already indexed with no original linked; fold
                            # the just-written original into its SQL row so delete,
                            # move, and reconvert see it without waiting for a boot.
                            await _sync_entry_from_disk_locked(store, safe)
                    status = "ok"
                except Exception as exc:
                    logger.warning(
                        "Failed to write original %s: %s",
                        relative_path,
                        exc,
                    )
                    _fail(relative_path)
                    status = "failed"
                yield _progress(relative_path, current, status)
                continue

            is_markdown = safe in preprocessed_markdown
            try:
                if is_markdown:
                    content_bytes = preprocessed_markdown[safe]
                else:
                    content_bytes = (extract_root / relative_path).read_bytes()
                await upload(
                    store, safe, content_bytes, spec=spec, llm=llm, origin="collection"
                )
                committed_stems.add(stem)
                # Count only on success, so the totals never overstate the import.
                if is_markdown:
                    markdown_count += 1
                else:
                    converted_count += 1
                status = "ok"
            except Exception as exc:
                logger.warning("Failed to process %s: %s", relative_path, exc)
                _fail(relative_path)
                status = "failed"

            yield _progress(relative_path, current, status)

    total_ok = markdown_count + converted_count
    yield CollectionCompleteEvent(
        total_files=total_ok,
        markdown_files=markdown_count,
        converted_attachments=converted_count,
        failed_files=failed,
        message=(
            f"Collection uploaded: {markdown_count} markdown, "
            f"{converted_count} processed attachments"
            + (f", {len(failed)} failed" if failed else "")
        ),
    )
