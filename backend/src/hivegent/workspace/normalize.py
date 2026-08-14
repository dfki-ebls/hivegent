"""Fold existing workspace paths and SQL stems to the canonical NFC spelling.

Every path entering the system is canonicalized at its boundary
(:func:`hivegent.config.sanitize_document_path` for the HTTP and workspace API,
:func:`hivegent.tools.base.resolve_search_path` for tool arguments), so new
content is NFC by construction.  This module repairs what predates that: a file
uploaded from macOS before the fix carries a decomposed name on disk and in
``documents.stem_path``, which a model can never address because it can only
emit the precomposed spelling.

Run through the admin API rather than at boot.  Disk and SQL agree with each
other today (both decomposed), so there is no drift to race, and one explicit
sweep repairs both sides together.  It stays idempotent and re-runnable for
content that arrives out of band later.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..config import normalize_unicode, settings
from ..db import documents as db_documents
from ..entries import description_path_for_stem, repoint_asset_refs
from ..store import Casebase
from ..text import read_text_file
from .locks import _locked_for
from .paths import _is_blocked_by_other

__all__ = ["NormalizeReport", "normalize_workspace_paths"]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class NormalizeReport:
    """Per-casebase summary of one normalization sweep."""

    files_renamed: int = 0
    stems_moved: int = 0
    collisions: int = 0


def _normalize_disk_paths(workspace: Path) -> tuple[int, int]:
    """Rename every non-NFC file and directory under *workspace* to NFC.

    Deepest first, rewriting only the final component, so an ancestor still
    carries the name the walk recorded when its children are renamed and its
    own rename afterwards carries them along.  Collecting the candidates up
    front is therefore safe: every path stays valid until its own turn.

    Returns:
        ``(renamed, collisions)``.
    """
    if not workspace.is_dir():
        return 0, 0

    # Filter before sorting: the walk is the whole store but the survivors are
    # typically none, so materialising and ordering every entry to act on a
    # handful is the expensive half.
    candidates = [
        path
        for path in workspace.rglob("*")
        if normalize_unicode(path.name) != path.name
    ]
    candidates.sort(key=lambda p: len(p.parts), reverse=True)

    renamed = 0
    collisions = 0
    for path in candidates:
        target = path.with_name(normalize_unicode(path.name))
        # `exists()` cannot tell "occupied by another file" from "the source
        # under its other spelling": a normalization-insensitive filesystem
        # (macOS) resolves both forms to the same inode, so an exists() guard
        # would skip every rename there.  Only a distinct inode is a blocker.
        if _is_blocked_by_other(target, path):
            logger.warning(
                "Skipping NFC rename of %s: %s already exists as another file",
                path,
                target,
            )
            collisions += 1
            continue

        path.rename(target)
        renamed += 1

    return renamed, collisions


def _repoint_asset_references(workspace: Path, src_stem: str, dst_stem: str) -> None:
    """Repoint a renamed entry's in-markdown ``<name>.assets/`` references.

    The same fix-up :func:`hivegent.workspace.move_document` performs after a
    rename, through the shared :func:`hivegent.entries.repoint_asset_refs`.  It
    rewrites one embedded path, never the body's own normalization.  Unlike the
    move, a sweep tolerates a description that is missing or not text: it
    repairs whatever it can reach rather than failing the whole run.
    """
    src_name = PurePosixPath(src_stem).name
    dst_name = PurePosixPath(dst_stem).name
    if src_name == dst_name:
        return

    description = workspace / description_path_for_stem(dst_stem)
    if not description.is_file():
        return

    decoded = read_text_file(description)
    if decoded is None:
        return

    updated = repoint_asset_refs(decoded.text, src_name, dst_name)
    if updated != decoded.text:
        description.write_text(updated, encoding="utf-8")


async def _normalize_sql_stems(store: Casebase, workspace: Path) -> tuple[int, int]:
    """Rewrite every non-NFC ``stem_path`` of *store* to NFC.

    One row at a time rather than :func:`hivegent.db.documents.move_subtree`:
    the transformation is per-string, so an entry's ``.assets`` children — rows
    in their own right — are folded by the same pass and need no prefix
    rewrite.  Chunks reference the immutable document id, so nothing is
    re-embedded.

    Returns:
        ``(moved, collisions)``.
    """
    stems = await db_documents.list_stem_paths(store)
    taken = set(stems)
    moved = 0
    collisions = 0

    for src in stems:
        dst = normalize_unicode(src)
        if dst == src:
            continue

        # ``(owner, stem_path)`` is unique.  The sweep holds the store lock and
        # has every stem in hand, so this check is exact and no IntegrityError
        # can reach the caller.  The loser keeps its old stem: dropping a row
        # cascades to its chunks, and deciding a document is redundant is the
        # orphan sweep's call, not this one's.
        if dst in taken:
            logger.warning(
                "Skipping NFC move of %s/%s: %r is already taken",
                store.store_key,
                src,
                dst,
            )
            collisions += 1
            continue

        await db_documents.move_document(store, src, store, dst)
        taken.add(dst)
        await asyncio.to_thread(_repoint_asset_references, workspace, src, dst)
        moved += 1

    return moved, collisions


async def normalize_workspace_paths(store: Casebase) -> NormalizeReport:
    """Fold every non-NFC workspace path and SQL stem of *store* to NFC.

    Idempotent and cheap on the common case: one walk and no renames.  The disk
    pass runs first so the SQL pass repoints asset references against files
    that are already canonically named.

    Args:
        store: The casebase to normalize.

    Returns:
        Counts of files renamed, SQL stems moved, and collisions skipped.
    """
    workspace = store.workspace_path(settings.data_dir)
    async with _locked_for(store, whole_store=True):
        renamed, disk_collisions = await asyncio.to_thread(
            _normalize_disk_paths, workspace
        )
        moved, sql_collisions = await _normalize_sql_stems(store, workspace)

    return NormalizeReport(
        files_renamed=renamed,
        stems_moved=moved,
        collisions=disk_collisions + sql_collisions,
    )
