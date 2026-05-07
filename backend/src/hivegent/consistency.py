"""Consistency between workspace files, chunk metadata, and the search index.

Two entry points:

- :func:`check_and_fix_all_stores` runs once at startup, before the app
  begins serving requests.
- :func:`run_periodic_consistency` loops in the background, calling the
  same per-store routine every ``interval_seconds`` so transient inline
  index failures self-heal without a restart.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from .chunks import chunk_document, delete_metadata
from .config import sanitize_group_id, sanitize_user_id, settings
from .converters.base import DOCUMENT_EXTENSION
from .retrieval import sync_index
from .store import Casebase
from .workspace import store_lock

__all__ = [
    "ConsistencyReport",
    "check_and_fix_all_stores",
    "check_store_consistency",
    "fix_store_consistency",
    "run_periodic_consistency",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ConsistencyReport:
    """Result of comparing documents, chunks, and index for one store.

    Attributes:
        store: The casebase that was checked.
        new_documents: Document paths that have no matching chunk file.
        stale_documents: Document paths whose mtime exceeds their chunk mtime.
        orphaned_chunks: Chunk files that have no matching document.
    """

    store: Casebase
    new_documents: list[str] = field(default_factory=list)
    stale_documents: list[str] = field(default_factory=list)
    orphaned_chunks: list[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        """Return True when all three lists are empty."""
        return (
            not self.new_documents
            and not self.stale_documents
            and not self.orphaned_chunks
        )


def check_store_consistency(store: Casebase) -> ConsistencyReport:
    """Compare documents and chunk files for a single store.

    Computes paths directly to avoid ``mkdir`` side effects from the
    directory helpers.  Only uses stat calls, never reads file content.

    Args:
        store: The casebase to check.

    Returns:
        A report describing any inconsistencies found.
    """
    docs_dir = store.workspace_path(settings.data_dir)
    metadata_dir = store.metadata_path(settings.data_dir)

    # Collect document relative paths and their mtimes.
    doc_mtimes: dict[str, float] = {}
    if docs_dir.exists():
        for path in docs_dir.rglob(f"*{DOCUMENT_EXTENSION}"):
            if path.is_file():
                rel = str(path.relative_to(docs_dir).as_posix())
                doc_mtimes[rel] = path.stat().st_mtime

    # Collect metadata relative paths and their mtimes.
    # Metadata files use stem-only naming: ``report.json`` for ``report.md``.
    chunk_mtimes: dict[str, float] = {}
    if metadata_dir.exists():
        for path in metadata_dir.rglob("*.json"):
            if path.is_file():
                stem = str(path.relative_to(metadata_dir).as_posix()).removesuffix(
                    ".json"
                )
                doc_key = stem + DOCUMENT_EXTENSION
                chunk_mtimes[doc_key] = path.stat().st_mtime

    doc_keys = set(doc_mtimes)
    chunk_keys = set(chunk_mtimes)

    new_documents = sorted(doc_keys - chunk_keys)
    orphaned_chunks = sorted(chunk_keys - doc_keys)
    stale_documents = sorted(
        rel for rel in doc_keys & chunk_keys if doc_mtimes[rel] > chunk_mtimes[rel]
    )

    return ConsistencyReport(
        store=store,
        new_documents=new_documents,
        stale_documents=stale_documents,
        orphaned_chunks=orphaned_chunks,
    )


async def fix_store_consistency(report: ConsistencyReport) -> None:
    """Repair inconsistencies described by a report.

    Deletes orphaned chunks and rechunks new/stale documents.  Each
    file operation is wrapped in try/except so a single bad file does
    not block the rest; both ``delete_metadata`` and ``chunk_document``
    maintain the LanceDB index inline.
    """
    if report.is_consistent:
        return

    store = report.store
    docs_dir = store.workspace_dir(settings.data_dir)

    for path in report.orphaned_chunks:
        try:
            await delete_metadata(store, path)
            logger.info("Deleted orphaned chunks: %s/%s", store.store_key, path)
        except Exception:
            logger.warning(
                "Failed to delete orphaned chunks: %s/%s",
                store.store_key,
                path,
                exc_info=True,
            )

    for path in report.new_documents + report.stale_documents:
        try:
            content = (docs_dir / path).read_text(encoding="utf-8")
            await chunk_document(store, path, content)
            logger.info("Chunked document: %s/%s", store.store_key, path)
        except Exception:
            logger.warning(
                "Failed to chunk document: %s/%s",
                store.store_key,
                path,
                exc_info=True,
            )


async def _check_and_fix_store(store: Casebase) -> None:
    """Check and fix a single store, holding the workspace lock throughout.

    Acquiring the lock serialises this routine against any in-flight
    workspace mutation so the periodic tick (which runs concurrently
    with HTTP traffic) cannot reindex a stale snapshot of a document.
    """
    async with store_lock(store):
        try:
            report = check_store_consistency(store)
            if report.is_consistent:
                logger.debug("Store %s is consistent", store.store_key)
            else:
                logger.info(
                    "Store %s: %d new, %d stale, %d orphaned",
                    store.store_key,
                    len(report.new_documents),
                    len(report.stale_documents),
                    len(report.orphaned_chunks),
                )
                await fix_store_consistency(report)

            # cbrkit diffs against existing rows inside ``create_index``,
            # so this also catches any documents whose inline index
            # write previously failed.
            try:
                await asyncio.to_thread(sync_index, store)
            except Exception:
                logger.warning(
                    "Failed to sync index for %s",
                    store.store_key,
                    exc_info=True,
                )
        except Exception:
            logger.warning(
                "Consistency check failed for %s", store.store_key, exc_info=True
            )


async def check_and_fix_all_stores() -> None:
    """Run the consistency check for every user and group store.

    Scans ``data/users/`` and ``data/groups/`` for store directories.
    Each store is handled independently so one broken store does not
    block the rest.
    """
    logger.info("Starting consistency check")

    if not settings.data_dir.exists():
        logger.info("Data directory does not exist, skipping consistency check")
        return

    # Check user stores
    users_dir = settings.data_dir / "users"
    if users_dir.exists():
        for entry in sorted(users_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                sanitize_user_id(entry.name)
            except ValueError:
                logger.debug("Skipping invalid user directory: %s", entry.name)
                continue
            if not (entry / "workspace").is_dir():
                logger.debug("Skipping user without workspace: %s", entry.name)
                continue
            await _check_and_fix_store(Casebase(kind="user", id=entry.name))

    # Check group stores
    groups_dir = settings.data_dir / "groups"
    if groups_dir.exists():
        for entry in sorted(groups_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                sanitize_group_id(entry.name)
            except ValueError:
                logger.debug("Skipping invalid group directory: %s", entry.name)
                continue
            if not (entry / "workspace").is_dir():
                logger.debug("Skipping group without workspace: %s", entry.name)
                continue
            await _check_and_fix_store(Casebase(kind="group", id=entry.name))

    logger.info("Consistency check complete")


async def run_periodic_consistency(interval_seconds: int) -> None:
    """Loop forever, calling :func:`check_and_fix_all_stores` every interval.

    Each iteration sleeps first so the startup pass (which the lifespan
    runs synchronously) isn't immediately followed by another full
    sweep.  The task is designed to be cancelled by the FastAPI
    lifespan shutdown — ``CancelledError`` is allowed to propagate.
    """
    logger.info("Periodic consistency tick every %ds", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await check_and_fix_all_stores()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Periodic consistency tick failed", exc_info=True)
