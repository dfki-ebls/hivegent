"""Startup consistency check between documents, chunks, and the search index."""

import logging
from dataclasses import dataclass, field

from .chunks import chunk_document, delete_chunks
from .config import DOCUMENT_EXTENSION, sanitize_user_id, settings
from .retrieval import sync_index

__all__ = [
    "ConsistencyReport",
    "check_and_fix_all_users",
    "check_user_consistency",
    "fix_user_consistency",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ConsistencyReport:
    """Result of comparing documents, chunks, and index for one user.

    Attributes:
        user_id: The user whose data was checked.
        new_documents: Document paths that have no matching chunk file.
        stale_documents: Document paths whose mtime exceeds their chunk mtime.
        orphaned_chunks: Chunk files that have no matching document.
    """

    user_id: str
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


def check_user_consistency(user_id: str) -> ConsistencyReport:
    """Compare documents and chunk files for a single user.

    Computes paths directly to avoid ``mkdir`` side effects from the
    ``get_user_*`` helpers.  Only uses stat calls, never reads file content.

    Args:
        user_id: The user to check.

    Returns:
        A report describing any inconsistencies found.
    """
    docs_dir = settings.data_dir / user_id / "documents"
    chunks_dir = settings.data_dir / user_id / "chunks"

    # Collect document relative paths and their mtimes.
    doc_mtimes: dict[str, float] = {}
    if docs_dir.exists():
        for path in docs_dir.rglob(f"*{DOCUMENT_EXTENSION}"):
            if path.is_file():
                rel = str(path.relative_to(docs_dir).as_posix())
                doc_mtimes[rel] = path.stat().st_mtime

    # Collect chunk relative paths (strip .json suffix) and their mtimes.
    chunk_mtimes: dict[str, float] = {}
    if chunks_dir.exists():
        for path in chunks_dir.rglob("*.json"):
            if path.is_file():
                rel = str(path.relative_to(chunks_dir).as_posix()).removesuffix(".json")
                chunk_mtimes[rel] = path.stat().st_mtime

    doc_keys = set(doc_mtimes)
    chunk_keys = set(chunk_mtimes)

    new_documents = sorted(doc_keys - chunk_keys)
    orphaned_chunks = sorted(chunk_keys - doc_keys)
    stale_documents = sorted(
        rel
        for rel in doc_keys & chunk_keys
        if doc_mtimes[rel] > chunk_mtimes[rel]
    )

    return ConsistencyReport(
        user_id=user_id,
        new_documents=new_documents,
        stale_documents=stale_documents,
        orphaned_chunks=orphaned_chunks,
    )


def fix_user_consistency(report: ConsistencyReport) -> None:
    """Repair inconsistencies described by a report.

    Deletes orphaned chunks, rechunks new and stale documents, then
    syncs the LanceDB index exactly once.  Each file operation is
    wrapped in try/except so a single bad file does not block the rest.

    Args:
        report: The consistency report to act on.
    """
    if report.is_consistent:
        return

    user_id = report.user_id
    docs_dir = settings.data_dir / user_id / "documents"

    for path in report.orphaned_chunks:
        try:
            delete_chunks(user_id, path)
            logger.info("Deleted orphaned chunks: %s/%s", user_id, path)
        except Exception:
            logger.warning(
                "Failed to delete orphaned chunks: %s/%s", user_id, path, exc_info=True
            )

    for path in report.new_documents + report.stale_documents:
        try:
            content = (docs_dir / path).read_text(encoding="utf-8")
            chunk_document(user_id, path, content)
            logger.info("Chunked document: %s/%s", user_id, path)
        except Exception:
            logger.warning(
                "Failed to chunk document: %s/%s", user_id, path, exc_info=True
            )

    try:
        sync_index(user_id)
        logger.info("Index synced for user %s", user_id)
    except Exception:
        logger.warning("Failed to sync index for user %s", user_id, exc_info=True)


def check_and_fix_all_users() -> None:
    """Run the consistency check for every user directory.

    Skips ``data_dir`` entries that are not valid user directories.
    Each user is handled independently so one broken user does not
    block the rest.
    """
    logger.info("Starting consistency check")

    if not settings.data_dir.exists():
        logger.info("Data directory does not exist, skipping consistency check")
        return

    for entry in sorted(settings.data_dir.iterdir()):
        if not entry.is_dir():
            continue

        try:
            sanitize_user_id(entry.name)
        except ValueError:
            logger.debug("Skipping non-user directory: %s", entry.name)
            continue

        if not (entry / "documents").is_dir():
            logger.debug("Skipping user without documents: %s", entry.name)
            continue

        try:
            report = check_user_consistency(entry.name)
            if report.is_consistent:
                logger.debug("User %s is consistent", entry.name)
            else:
                logger.info(
                    "User %s: %d new, %d stale, %d orphaned",
                    entry.name,
                    len(report.new_documents),
                    len(report.stale_documents),
                    len(report.orphaned_chunks),
                )
                fix_user_consistency(report)
        except Exception:
            logger.warning(
                "Consistency check failed for user %s", entry.name, exc_info=True
            )

    logger.info("Consistency check complete")
