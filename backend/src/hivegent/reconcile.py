"""Startup reconciliation between workspace, SQL, and LanceDB.

SQL is the source of truth.  Workspace files and LanceDB rows are
derived projections; on boot, this module prunes anything that SQL does
not vouch for and re-derives anything SQL says should exist.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from .chunks import delete_document as _delete_chunked_document
from .config import settings
from .db import documents as db_documents
from .entries import cleanup_empty_parents, is_assets_dir, stem_path_from_reference
from .retrieval import index_document, list_indexed_filenames, unindex_paths
from .store import Casebase
from .workspace import store_lock

__all__ = [
    "ReconcileReport",
    "reconcile_all",
    "reconcile_store",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ReconcileReport:
    """Per-casebase summary of work done during reconciliation."""

    disk_orphans_removed: int = 0
    sql_orphans_removed: int = 0
    lance_orphans_removed: int = 0
    lance_reindexed: int = 0


def _owning_stem_for_path(rel: PurePosixPath) -> str:
    """Return the SQL stem that owns the file at *rel*, walking ``.assets``.

    Files inside ``foo.assets/`` belong to the document with stem
    ``foo``; nested asset subtrees collapse to the outermost owner.
    """
    parts = rel.parts
    for i, part in enumerate(parts):
        if not is_assets_dir(part):
            continue
        stem = part.removesuffix(".assets")
        if stem:
            return "/".join((*parts[:i], stem))
    return stem_path_from_reference(str(rel))


async def _sweep_disk_orphans(store: Casebase, sql_stems: set[str]) -> int:
    """Delete workspace files whose owning stem is not in SQL."""
    workspace = store.workspace_path(settings.data_dir)
    if not workspace.exists():
        return 0
    removed = 0
    for file_path in workspace.rglob("*"):
        if not file_path.is_file():
            continue
        rel = PurePosixPath(file_path.relative_to(workspace).as_posix())
        if _owning_stem_for_path(rel) in sql_stems:
            continue
        try:
            file_path.unlink()
            cleanup_empty_parents(file_path, workspace)
        except OSError:
            logger.warning(
                "Failed to remove disk orphan %s/%s",
                store.store_key,
                rel,
                exc_info=True,
            )
            continue
        removed += 1
    return removed


async def _sweep_sql_orphans(store: Casebase, sql_paths: dict[str, int]) -> int:
    """Drop SQL documents whose description file is missing on disk.

    Mutates *sql_paths* in place to drop successfully-removed entries so
    the caller observes the post-sweep SQL state.
    """
    workspace = store.workspace_path(settings.data_dir)
    missing = [path for path in sql_paths if not (workspace / path).exists()]
    removed = 0
    for description_path in missing:
        try:
            await _delete_chunked_document(store, description_path)
        except Exception:
            logger.warning(
                "Failed to remove SQL orphan %s/%s",
                store.store_key,
                description_path,
                exc_info=True,
            )
            continue
        sql_paths.pop(description_path, None)
        removed += 1
    return removed


async def _sweep_lance_orphans(
    store: Casebase, indexed: set[str], sql_filenames: set[str]
) -> int:
    """Drop LanceDB rows whose filename is unknown to SQL."""
    orphans = indexed - sql_filenames
    if not orphans:
        return 0
    try:
        await unindex_paths(store, orphans)
    except Exception:
        logger.warning(
            "Failed to drop LanceDB orphans for %s",
            store.store_key,
            exc_info=True,
        )
        return 0
    return len(orphans)


async def _sweep_lance_reindex(
    store: Casebase, indexed: set[str], sql_paths: Mapping[str, int]
) -> int:
    """Re-index SQL documents that have chunks but no LanceDB rows."""
    missing = {path for path, count in sql_paths.items() if count > 0} - indexed
    if not missing:
        return 0
    reindexed = 0
    for description_path in missing:
        try:
            doc = await db_documents.get_document(store, description_path)
            if doc is None:
                continue
            await index_document(store, description_path, doc)
        except Exception:
            logger.warning(
                "Failed to re-index %s/%s",
                store.store_key,
                description_path,
                exc_info=True,
            )
            continue
        reindexed += 1
    return reindexed


async def reconcile_store(store: Casebase) -> ReconcileReport:
    """Bring *store*'s three layers back into agreement, with SQL as truth."""
    async with store_lock(store):
        sql_paths = dict(await db_documents.list_document_paths(store))
        sql_stems = {stem_path_from_reference(path) for path in sql_paths}

        disk_removed = await _sweep_disk_orphans(store, sql_stems)
        sql_removed = await _sweep_sql_orphans(store, sql_paths)
        try:
            indexed = await list_indexed_filenames(store)
        except Exception:
            logger.warning(
                "Failed to enumerate LanceDB filenames for %s",
                store.store_key,
                exc_info=True,
            )
            indexed = set()
        lance_removed = await _sweep_lance_orphans(store, indexed, set(sql_paths))
        lance_reindexed = await _sweep_lance_reindex(store, indexed, sql_paths)

        return ReconcileReport(
            disk_orphans_removed=disk_removed,
            sql_orphans_removed=sql_removed,
            lance_orphans_removed=lance_removed,
            lance_reindexed=lance_reindexed,
        )


def _disk_known_stores() -> set[Casebase]:
    """Return casebases with an existing workspace directory."""
    root = settings.data_dir / "workspace"
    if not root.is_dir():
        return set()
    stores: set[Casebase] = set()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            stores.add(Casebase.from_store_key(child.name))
        except ValueError:
            logger.warning("Ignoring unrecognised workspace directory: %s", child.name)
    return stores


async def reconcile_all() -> Mapping[str, ReconcileReport]:
    """Reconcile every casebase known to SQL or present on disk."""
    sql_stores = await db_documents.list_known_stores()
    stores = sql_stores | _disk_known_stores()
    reports: dict[str, ReconcileReport] = {}
    for store in sorted(stores, key=lambda s: s.store_key):
        try:
            reports[store.store_key] = await reconcile_store(store)
        except Exception:
            logger.warning("Reconciliation failed for %s", store.store_key, exc_info=True)
    return reports
