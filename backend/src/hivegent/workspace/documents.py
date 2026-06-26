"""Public single-entry text mutations and logical-entry moves and deletes.

Edit, write, rechunk, delete, and move a single logical entry.  Each acquires
the casebase lock through :func:`_locked_for` and runs its file + SQL step to
completion under a cancel so the workspace and its index never drift apart.
"""

from pathlib import Path, PurePosixPath

from fastapi import HTTPException

# Module-object import (absolute path) keeps test seams patchable and out of a cycle.
import hivegent.workspace.indexing as indexing
from ..chunkers import ChunkingSpec
from ..chunkers.base import DocumentMetadata
from ..concurrency import shield_to_completion
from ..config import content_hash, sanitize_document_path, settings
from ..db import documents as db_documents
from ..converters.base import DOCUMENT_EXTENSION
from ..entries import (
    ContentStat,
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    is_description_file,
    stem_path_from_reference,
)
from ..store import Casebase
from ..types import MoveDocumentResponse, PipelineSpec
from .commit import _delete_single_locked
from .locks import _locked_for
from .paths import (
    _check_destination_parents,
    _check_not_assets_path,
    _enforce_file_size,
    _is_blocked_by_other,
    _is_same_file,
    _resolve_move_destination,
)

__all__ = [
    "delete_document",
    "edit_document_text",
    "move_document",
    "rechunk",
    "write_document_text",
]


def _read_text_file(file_path: Path) -> str:
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return file_path.read_text(encoding="utf-8")


def _check_expected_hash(
    safe: str, current: str | None, expected_hash: str | None
) -> None:
    """Reject the mutation unless *current* still matches *expected_hash*.

    The optimistic-concurrency guard: a non-``None`` *expected_hash* comes
    from an earlier read. A mismatch means the document moved on since, and a
    missing file (*current* is ``None``) means the caller never read it at all,
    so the hash is hallucinated. Both are rejected with a 409.
    """
    if expected_hash is None:
        return
    if current is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{safe}' does not exist, so it could not have been read "
                f"(expected hash {expected_hash}); omit expected_hash to create it"
            ),
        )
    if (actual := content_hash(current)) != expected_hash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{safe}' changed since it was read "
                f"(expected hash {expected_hash}, found {actual}); "
                "re-read it and retry with the new hash"
            ),
        )


async def _replace_text_locked(
    store: Casebase,
    safe: str,
    full_path: Path,
    content: str,
    chunking: ChunkingSpec | None = None,
) -> None:
    # The content is always chunked as the markdown description at
    # ``<stem>.md``; writing it to a non-markdown path would leave the on-disk
    # file and the indexed description divergent, so the chunk count could never
    # be matched back to the entry.  Reject up front to keep disk and SQL in sync.
    if not is_description_file(safe):
        raise HTTPException(
            status_code=400,
            detail=f"Only markdown documents can be written: '{safe}' must end in '{DOCUMENT_EXTENSION}'.",
        )

    _enforce_file_size(content.encode("utf-8"))
    if full_path.is_dir():
        raise HTTPException(status_code=400, detail=f"'{safe}' is a directory")
    _check_destination_parents(store.workspace_dir(settings.data_dir), safe)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    await shield_to_completion(
        indexing.chunk_and_index_document(
            store, safe, content, chunking, stat=ContentStat.from_path(full_path)
        )
    )


async def rechunk(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
) -> DocumentMetadata:
    """Re-chunk an existing markdown document.

    The chunk + embed + SQL upsert runs to completion even on a cancel
    (:func:`shield_to_completion`), so it finishes under the lock and cannot
    tear the workspace markdown out of sync with SQL.
    """
    spec = spec or PipelineSpec()
    async with _locked_for(store, safe):
        file_path = store.workspace_dir(settings.data_dir) / safe
        text = _read_text_file(file_path)
        return await shield_to_completion(
            indexing.chunk_and_index_document(
                store, safe, text, spec.chunking, stat=ContentStat.from_path(file_path)
            )
        )


async def edit_document_text(
    store: Casebase,
    safe: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_hash: str | None = None,
) -> str:
    """Edit a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with _locked_for(store, safe):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        content = _read_text_file(file_path)
        _check_expected_hash(safe, content, expected_hash)
        count = content.count(old_string)
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"old_string not found in '{safe}'",
            )
        if count > 1 and not replace_all:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"old_string appears {count} times in '{safe}'; "
                    "must be unique or call with replace_all=True"
                ),
            )
        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        await _replace_text_locked(store, safe, file_path, new_content)
    replaced = count if replace_all else 1
    noun = "occurrence" if replaced == 1 else "occurrences"
    return f"Replaced {replaced} {noun} in '{safe}'."


async def write_document_text(
    store: Casebase,
    safe: str,
    content: str,
    mode: str = "replace",
    expected_hash: str | None = None,
    chunking: ChunkingSpec | None = None,
) -> str:
    """Write a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with _locked_for(store, safe):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        current = file_path.read_text(encoding="utf-8") if file_path.is_file() else None
        _check_expected_hash(safe, current, expected_hash)
        if mode == "replace":
            new_content = content
            message = f"Wrote {len(content)} characters to '{safe}'."
        elif mode == "create":
            if current is not None:
                raise HTTPException(status_code=409, detail=f"'{safe}' already exists")
            new_content = content
            message = f"Created '{safe}' with {len(content)} characters."
        elif current is None:
            raise HTTPException(
                status_code=404,
                detail=f"'{safe}' does not exist (use mode='replace' to create)",
            )
        elif mode == "append":
            new_content = current + content
            message = f"Appended {len(content)} characters to '{safe}'."
        elif mode == "prepend":
            new_content = content + current
            message = f"Prepended {len(content)} characters to '{safe}'."
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported write mode: {mode}"
            )
        await _replace_text_locked(store, safe, file_path, new_content, chunking)
    return message


async def delete_document(store: Casebase, safe: str) -> None:
    """Delete a logical entry and all of its files.

    The file + SQL removal runs to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so it cannot leave files without their rows
    or rows without their files.
    """
    async with _locked_for(store, safe):
        await shield_to_completion(_delete_single_locked(store, safe))


async def _move_document_locked(
    store: Casebase, src: str, dst: str
) -> MoveDocumentResponse:
    """Move a logical entry's files and SQL rows. Caller holds the lock."""
    workspace_dir = store.workspace_dir(settings.data_dir)

    metadata = await db_documents.get_document(store, src)
    if not metadata or not (workspace_dir / metadata.description_path).exists():
        raise HTTPException(status_code=404, detail="Document not found")
    src_stem = metadata.stem_path
    src_description_full = workspace_dir / metadata.description_path

    # Move-into resolution appends the description *filename* (a reference),
    # never the bare stem name: ``stem_path_from_reference`` strips the last
    # dotted segment, so a stem like ``report.v1`` passed back through it
    # would collapse to ``report``.
    dst_stem = stem_path_from_reference(
        _resolve_move_destination(
            workspace_dir,
            PurePosixPath(metadata.description_path).name,
            dst,
            src_description_full,
        )
    )
    _check_not_assets_path(dst_stem)
    if dst_stem == src_stem:
        raise HTTPException(
            status_code=400,
            detail=(
                "Source and destination refer to the same document; "
                "extensions are derived from the entry and cannot change by moving"
            ),
        )

    dst_description = description_path_for_stem(dst_stem)

    # Plan every rename up front and validate all destinations before touching
    # the filesystem: a conflict discovered halfway through the renames would
    # tear the entry into two half-moved halves.
    renames: list[tuple[str, str]] = [(metadata.description_path, dst_description)]
    if metadata.original_path and (workspace_dir / metadata.original_path).exists():
        suffix = PurePosixPath(metadata.original_path).suffix
        renames.append((metadata.original_path, f"{dst_stem}{suffix}"))
    has_assets = (
        metadata.assets_dir is not None
        and (workspace_dir / metadata.assets_dir).exists()
    )
    if metadata.assets_dir is not None and has_assets:
        renames.append((metadata.assets_dir, assets_dir_for_stem(dst_stem)))

    _check_destination_parents(workspace_dir, dst_stem)
    # A destination occupied by the entry itself is a case-only rename on a
    # case-insensitive filesystem, which a plain rename handles fine.
    same_entry = _is_same_file(workspace_dir / dst_description, src_description_full)
    # entry_exists takes a reference, so hand it the description path: a raw
    # dotted stem would be re-stemmed and the wrong entry checked.
    if not same_entry and entry_exists(workspace_dir, dst_description):
        raise HTTPException(status_code=409, detail="Destination already exists")
    for source, target in renames:
        if _is_blocked_by_other(workspace_dir / target, workspace_dir / source):
            raise HTTPException(
                status_code=409, detail=f"Destination already exists: {target}"
            )

    for source, target in renames:
        target_path = workspace_dir / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        (workspace_dir / source).rename(target_path)

    src_name = PurePosixPath(src_stem).name
    dst_name = PurePosixPath(dst_stem).name
    if has_assets and src_name != dst_name:
        # The markdown references its assets as ``<stem>.assets/...``; rewrite
        # those references when the stem's basename changed.
        description_full = workspace_dir / dst_description
        body = description_full.read_text(encoding="utf-8")
        body = body.replace(f"{src_name}.assets/", f"{dst_name}.assets/")
        description_full.write_text(body, encoding="utf-8")

    # Move exactly this entry's row; a same-named sibling directory's rows
    # (stems below ``src_stem/``) belong to other documents and stay put.
    await db_documents.move_document(store, src_stem, dst_stem)
    # The described/extracted asset children live under the ``.assets`` sibling
    # of ``src_stem`` — move their rows too so nothing stays searchable at a
    # path that no longer exists.
    if metadata.assets_dir:
        await db_documents.move_subtree(
            store, metadata.assets_dir, assets_dir_for_stem(dst_stem)
        )

    return MoveDocumentResponse(
        source=src,
        destination=dst_description,
        message="Document moved successfully",
    )


async def move_document(store: Casebase, src: str, dst: str) -> MoveDocumentResponse:
    """Move a logical entry, its original, and its child-assets subtree.

    The FS renames + SQL move run to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so a mid-move cancellation cannot leave the
    renamed files pointing at stale SQL rows.
    """
    async with _locked_for(store, src):
        return await shield_to_completion(_move_document_locked(store, src, dst))
