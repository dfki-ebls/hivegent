"""ZIP collection import.

Validate a ZIP archive against the configured limits, then feed each file
through the phased :func:`upload`.  The whole import holds a store claim so a
concurrent store-wide delete or directory move cannot interleave between files
and strip entries the collection already committed.
"""

import logging
import stat
import tempfile
import zipfile
import zlib
from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..config import sanitize_document_path, settings
from ..converters.base import DOCUMENT_EXTENSION
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
]

logger = logging.getLogger(__name__)


def _validate_zip_entries(archive: zipfile.ZipFile) -> None:
    """Reject unsafe ZIP entries before extraction.

    Catches symlinks, special files, traversal paths, and zip bombs
    (per-entry and cumulative uncompressed size).
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


async def process_collection(
    store: Casebase,
    archive_path: Path,
    spec: PipelineSpec,
    llm: LlmConfig,
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent]:
    """Process a ZIP collection and yield progress events for each file.

    Each file flows through the phased :func:`upload`, which holds the casebase
    lock only for its brief reserve and commit — so the rest of the workspace
    stays responsive while a large collection processes, and a cancel mid-run
    rolls back only the in-flight file while earlier files survive.  The whole
    import holds a store claim (:func:`_store_claim`) so a concurrent store-wide
    delete or directory move cannot interleave between files and strip entries
    the collection already committed.
    """
    failed: list[str] = []
    markdown_count = 0
    converted_count = 0
    current = 0

    with _store_claim(store), tempfile.TemporaryDirectory() as tmp_dir:
        extract_root = Path(tmp_dir)

        try:
            with zipfile.ZipFile(archive_path) as archive:
                _validate_zip_entries(archive)
                archive.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
        except zlib.error as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to decompress ZIP: {exc!s}",
            ) from exc

        top_items = list(extract_root.iterdir())
        if len(top_items) == 1 and top_items[0].is_dir():
            extract_root = top_items[0]

        collection_files = sorted(
            str(path.relative_to(extract_root).as_posix())
            for path in extract_root.rglob("*")
            if path.is_file()
        )
        workspace_dir = store.workspace_dir(settings.data_dir)
        preprocessed_markdown: dict[str, bytes] = {}
        collection_stems: set[str] = set()
        companion_originals: set[str] = set()

        for relative_path in collection_files:
            safe = sanitize_document_path(relative_path)
            suffix = PurePosixPath(safe).suffix.lower()
            if suffix == DOCUMENT_EXTENSION:
                try:
                    text = (extract_root / relative_path).read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", relative_path, exc)
                    failed.append(relative_path)
                    continue
                normalized_md = preprocess_markdown(
                    text, safe, frozenset(collection_files)
                )
                preprocessed_markdown[safe] = normalized_md.content.encode("utf-8")

            stem = stem_path_from_reference(safe)
            if entry_exists(workspace_dir, safe):
                failed.append(relative_path)
                continue
            if stem in collection_stems:
                if suffix != DOCUMENT_EXTENSION:
                    companion_originals.add(relative_path)
                else:
                    failed.append(relative_path)
                continue
            collection_stems.add(stem)

        total = len(collection_files)
        for relative_path in collection_files:
            safe = sanitize_document_path(relative_path)
            if relative_path in failed:
                current += 1
                yield CollectionProgressEvent(
                    file=relative_path,
                    current=current,
                    total=total,
                    status="failed",
                )
                continue

            if relative_path in companion_originals:
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
                    failed.append(relative_path)
                    status = "failed"
                current += 1
                yield CollectionProgressEvent(
                    file=relative_path,
                    current=current,
                    total=total,
                    status=status,
                )
                continue

            try:
                if safe in preprocessed_markdown:
                    content_bytes = preprocessed_markdown[safe]
                    markdown_count += 1
                else:
                    content_bytes = (extract_root / relative_path).read_bytes()
                    converted_count += 1
                await upload(
                    store, safe, content_bytes, spec=spec, llm=llm, origin="collection"
                )
                status = "ok"
            except Exception as exc:
                logger.warning("Failed to process %s: %s", relative_path, exc)
                if safe in preprocessed_markdown:
                    markdown_count -= 1
                else:
                    converted_count -= 1
                failed.append(relative_path)
                status = "failed"

            current += 1
            yield CollectionProgressEvent(
                file=relative_path,
                current=current,
                total=total,
                status=status,
            )

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
