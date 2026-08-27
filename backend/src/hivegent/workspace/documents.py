"""Public single-entry text mutations and logical-entry moves and deletes.

Edit, write, rechunk, delete, and move a single logical entry.  Each acquires
the casebase lock through :func:`_locked_for` and runs its file + SQL step to
completion under a cancel so the workspace and its index never drift apart.
"""

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..chunkers import ChunkingSpec
from ..chunkers.base import DocumentMetadata
from ..concurrency import shield_to_completion
from ..config import content_hash, sanitize_document_path, settings
from ..converters import BINARY_WRITE_REASON, vision_media_type, writes_as_text
from ..db import documents as db_documents
from ..entries import (
    SCRATCH_DIR_NAME,
    ContentStat,
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    is_description_file,
    is_scratch_path,
    repoint_asset_refs,
    stem_path_from_reference,
)
from ..humanize import pluralize
from ..llm_config import LlmConfig
from ..store import Casebase
from ..text import NOT_TEXT_REASON, DecodedText, read_text_file
from ..types import PipelineSpec
from .commit import (
    _delete_single_locked,
    _ensure_upload_slot_locked,
    _phased_upload,
)
from .indexing import chunk_and_index_document
from .locks import _locked_for, _reject_if_inflight
from .paths import (
    _check_destination_parents,
    _check_not_reserved_path,
    _enforce_file_size,
    _is_blocked_by_other,
    _is_same_file,
    _resolve_move_destination,
    _shown,
    _write_workspace_file,
)
from .prepare import _Reserved

__all__ = [
    "delete_document",
    "edit_document_text",
    "move_document",
    "rechunk",
    "write_document_text",
]


def _decode_existing(file_path: Path, shown: str) -> DecodedText:
    """Decode an existing workspace file, rejecting content that is not text.

    Reads go through the shared decoder so a legacy-encoded file is editable
    and hashes the same way the read tools hash it; the rewrite is UTF-8, which
    normalises the file on its first edit.
    """
    decoded = read_text_file(file_path)
    if decoded is None:
        raise HTTPException(
            status_code=422,
            detail=f"'{shown}' {NOT_TEXT_REASON}",
        )
    return decoded


def _editable_text(file_path: Path, shown: str) -> DecodedText | None:
    """Return a document's decoded text, or ``None`` when it does not exist.

    The gate on every text mutation, not merely a read: it raises for anything
    that may not be edited at all.  Both halves of that question are the ones
    ``read_document`` asks, in the same order — the shared vision-media table
    first, the shared decoder second — so a document is writable exactly when it
    is readable, rather than merely being described that way in two tool
    descriptions.
    """
    if file_path.is_dir():
        raise HTTPException(status_code=409, detail=f"'{shown}' is a directory")
    if (media_type := vision_media_type(file_path.name)) is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{shown}' is a {media_type} binary and cannot be written as "
                "text; upload a replacement instead"
            ),
        )
    return _decode_existing(file_path, shown) if file_path.is_file() else None


def _transcode_note(source_encoding: str | None) -> str:
    """Report the UTF-8 normalization of legacy-encoded existing content."""
    if source_encoding is None:
        return ""

    return f" The file was transcoded from {source_encoding} to UTF-8."


def _check_expected_hash(
    shown: str, current: str | None, expected_hash: str | None
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
                f"'{shown}' does not exist, so it could not have been read "
                f"(expected hash {expected_hash}); omit expected_hash to create it"
            ),
        )
    if (actual := content_hash(current)) != expected_hash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{shown}' changed since it was read "
                f"(expected hash {expected_hash}, found {actual}); "
                "re-read it and retry with the new hash"
            ),
        )


type _TextMutation = Callable[[str | None], tuple[str, str]]
"""Derive a document's new content and its report from its current content.

The current content is ``None`` for a document that does not exist yet.  Being
a plain function is what lets the same edit and write semantics run at both of
the persistence paths' quite different moments.
"""


def _edit_mutation(
    shown: str, old_string: str, new_string: str, replace_all: bool
) -> _TextMutation:
    """Build the exact-string replacement behind :func:`edit_document_text`."""

    def mutate(current: str | None) -> tuple[str, str]:
        if current is None:
            raise HTTPException(status_code=404, detail="Document not found")
        count = current.count(old_string)
        if count == 0:
            raise HTTPException(
                status_code=422,
                detail=f"old_string not found in '{shown}'",
            )
        if count > 1 and not replace_all:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"old_string appears {count} times in '{shown}'; "
                    "must be unique or call with replace_all=True"
                ),
            )
        replaced = count if replace_all else 1
        return (
            current.replace(old_string, new_string, -1 if replace_all else 1),
            f"Replaced {replaced} {pluralize(replaced, 'occurrence')} in '{shown}'.",
        )

    return mutate


def _write_mutation(shown: str, content: str, mode: str) -> _TextMutation:
    """Build the write-mode composition behind :func:`write_document_text`."""
    if mode not in ("replace", "create", "append", "prepend"):
        raise HTTPException(status_code=400, detail=f"Unsupported write mode: {mode}")

    def mutate(current: str | None) -> tuple[str, str]:
        if mode == "replace":
            return content, f"Wrote {len(content)} characters to '{shown}'."
        if mode == "create":
            if current is not None:
                raise HTTPException(status_code=409, detail=f"'{shown}' already exists")
            return content, f"Created '{shown}' with {len(content)} characters."
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=f"'{shown}' does not exist (use mode='replace' to create)",
            )
        if mode == "append":
            return (
                current + content,
                f"Appended {len(content)} characters to '{shown}'.",
            )
        return content + current, f"Prepended {len(content)} characters to '{shown}'."

    return mutate


async def _rewrite_in_place(
    store: Casebase,
    safe: str,
    mutate: _TextMutation,
    expected_hash: str | None,
    chunking: ChunkingSpec | None,
) -> str:
    """Rewrite a file whose own bytes are the content, indexing it where it lies.

    Two kinds of file are their own content and derive nothing: a markdown
    description, which *is* the indexed text, and a scratch file, which is
    deliberately never indexed at all.  They share every step but the last, so
    they share the body and differ by one branch rather than by a second copy
    of it.

    The lock is not only for the index: it spans read, hash check, and write, so
    ``expected_hash`` is genuine optimistic concurrency rather than a check
    followed by a hopeful write, and :func:`_locked_for` rejects a mutation that
    collides with a phased upload.  The chunk + embed + upsert is shielded so a
    cancel cannot leave the new markdown wearing the rows of the old.
    """
    async with _locked_for(store, safe):
        workspace_dir = store.workspace_dir(settings.data_dir)
        shown = _shown(store, safe)
        decoded = _editable_text(workspace_dir / safe, shown)
        current = decoded.text if decoded is not None else None
        _check_expected_hash(shown, current, expected_hash)
        content, message = mutate(current)
        data = content.encode("utf-8")
        _enforce_file_size(data)
        _check_destination_parents(store, safe)
        written = _write_workspace_file(workspace_dir, safe, data)
        # Scratch stops here: no rows, and so no fingerprint to stamp them with.
        if not is_scratch_path(safe):
            await shield_to_completion(
                chunk_and_index_document(
                    store, safe, content, chunking, stat=ContentStat.from_path(written)
                )
            )
    return message + _transcode_note(decoded.source_encoding if decoded else None)


async def _rewrite_original(
    store: Casebase,
    safe: str,
    mutate: _TextMutation,
    expected_hash: str | None,
    chunking: ChunkingSpec | None,
) -> str:
    """Rewrite a text original and regenerate the projection derived from it.

    The new bytes run through the same reserve → prepare → commit lifecycle as
    an upload of the edited file (:func:`_phased_upload`), so the ``<stem>.md``
    left behind is byte-for-byte the one uploading it would produce, stale
    assets are cleared with it, and a failed conversion leaves the previous
    entry untouched.  Read, hash check, and mutation all happen inside the
    reserve, under the casebase lock, so the optimistic-concurrency guarantee
    matches the description path's.

    A file with no description yet — dropped into the workspace by hand — is
    promoted to a full entry by the same commit, since an editable original
    always has an indexed projection.
    """
    spec = PipelineSpec(chunking=chunking) if chunking else PipelineSpec()
    # The reserve owns the mutation, so its report comes back through this
    # single-slot cell instead of being recomputed against re-read bytes.
    report: list[str] = []

    async def reserve() -> _Reserved:
        workspace_dir = store.workspace_dir(settings.data_dir)
        shown = _shown(store, safe)
        decoded = _editable_text(workspace_dir / safe, shown)
        current = decoded.text if decoded is not None else None
        _check_expected_hash(shown, current, expected_hash)
        # Mutating first lets an operation that needs the document to exist say
        # so, rather than being answered with a rule about creating one.
        content, message = mutate(current)
        if current is None and not writes_as_text(safe):
            raise HTTPException(
                status_code=400, detail=f"'{shown}' {BINARY_WRITE_REASON}"
            )
        # Creating an original claims the whole stem: requiring a free one keeps
        # the write from superseding another entry's description or original.
        _ensure_upload_slot_locked(store, safe, overwrite=current is not None)
        data = content.encode("utf-8")
        _enforce_file_size(data)
        report.append(
            message + _transcode_note(decoded.source_encoding if decoded else None)
        )
        metadata = await db_documents.get_entry_metadata(store, safe)
        return _Reserved(
            reference=safe,
            content=data,
            origin=metadata.origin if metadata else "imported",
            original_path=safe,
            original_content=data,
            preserve=current is not None,
        )

    await _phased_upload(
        store, spec, LlmConfig(), stem_reference=safe, reserve=reserve, ctx=None
    )
    description = description_path_for_stem(stem_path_from_reference(safe))
    return (
        f"{report[0]} Its searchable markdown '{_shown(store, description)}' was "
        f"regenerated from the new content."
    )


async def _apply_text_mutation(
    store: Casebase,
    safe: str,
    mutate: _TextMutation,
    expected_hash: str | None,
    chunking: ChunkingSpec | None = None,
) -> str:
    """Persist a text mutation, keeping the entry's projection in step with it.

    Location decides first — a scratch path is never an entry — and only then
    does the name decide where an entry write lands: on the indexed description
    itself, or on an original whose description has to be re-derived from it.
    """
    if PurePosixPath(safe).name == SCRATCH_DIR_NAME:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{_shown(store, safe)}' names a scratch directory, not a "
                "writable file"
            ),
        )

    if is_scratch_path(safe) or is_description_file(safe):
        return await _rewrite_in_place(store, safe, mutate, expected_hash, chunking)
    return await _rewrite_original(store, safe, mutate, expected_hash, chunking)


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
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        decoded = _editable_text(file_path, _shown(store, safe))
        if decoded is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return await shield_to_completion(
            chunk_and_index_document(
                store,
                safe,
                decoded.text,
                spec.chunking,
                stat=ContentStat.from_path(file_path),
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
    return await _apply_text_mutation(
        store,
        safe,
        _edit_mutation(_shown(store, safe), old_string, new_string, replace_all),
        expected_hash,
    )


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
    return await _apply_text_mutation(
        store,
        safe,
        _write_mutation(_shown(store, safe), content, mode),
        expected_hash,
        chunking,
    )


async def delete_document(store: Casebase, safe: str) -> None:
    """Delete a logical entry and all of its files.

    The file + SQL removal runs to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so it cannot leave files without their rows
    or rows without their files.
    """
    async with _locked_for(store, safe):
        await shield_to_completion(_delete_single_locked(store, safe))


async def _move_document_locked(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> None:
    """Move a logical entry's files and SQL rows. Caller holds the lock(s).

    Source paths resolve under *src_store*'s workspace and destination paths
    under *dst_store*'s, so the same machinery relocates an entry within one
    workspace or migrates it to another (personal ↔ group, group ↔ group).
    """
    src_workspace = src_store.workspace_dir(settings.data_dir)
    # Non-creating: the rename step below makes the destination tree where the
    # files actually land, so validation never needs the directory to exist and
    # a rejected move leaves no empty workspace behind.
    dst_workspace = dst_store.workspace_path(settings.data_dir)
    cross_store = src_store != dst_store

    metadata = await db_documents.get_entry_metadata(src_store, src)
    if not metadata or not (src_workspace / metadata.description_path).exists():
        raise HTTPException(status_code=404, detail="Document not found")
    src_stem = metadata.stem_path
    src_description_full = src_workspace / metadata.description_path

    # Move-into resolution appends the description *filename* (a reference),
    # never the bare stem name: ``stem_path_from_reference`` strips the last
    # dotted segment, so a stem like ``report.v1`` passed back through it
    # would collapse to ``report``.
    dst_stem = stem_path_from_reference(
        _resolve_move_destination(
            dst_workspace,
            PurePosixPath(metadata.description_path).name,
            dst,
            src_description_full,
        )
    )
    _check_not_reserved_path(dst_stem)
    # Same stem in the same store is a no-op; across stores it is a genuine
    # re-home of the entry, so only reject the in-place case.
    if not cross_store and dst_stem == src_stem:
        raise HTTPException(
            status_code=400,
            detail=(
                "Source and destination refer to the same document; "
                "extensions are derived from the entry and cannot change by moving"
            ),
        )

    dst_description = description_path_for_stem(dst_stem)
    _reject_if_inflight(dst_store, dst_description)

    # Plan every rename up front and validate all destinations before touching
    # the filesystem: a conflict discovered halfway through the renames would
    # tear the entry into two half-moved halves.
    renames: list[tuple[str, str]] = [(metadata.description_path, dst_description)]
    if metadata.original_path and (src_workspace / metadata.original_path).exists():
        suffix = PurePosixPath(metadata.original_path).suffix
        renames.append((metadata.original_path, f"{dst_stem}{suffix}"))
    # Only an assets directory that is actually on disk is renamed; its SQL rows
    # move below either way, since a recorded directory that vanished can still
    # have child rows pointing at the old path.
    moved_assets = (
        metadata.assets_dir
        if metadata.assets_dir and (src_workspace / metadata.assets_dir).exists()
        else None
    )
    if moved_assets is not None:
        renames.append((moved_assets, assets_dir_for_stem(dst_stem)))

    _check_destination_parents(dst_store, dst_stem)
    # A destination occupied by the entry itself is a case-only rename on a
    # case-insensitive filesystem, which a plain rename handles fine.  A
    # cross-store destination lives in a different tree and never aliases the
    # source, so this is only ever true for an in-place rename.
    same_entry = _is_same_file(dst_workspace / dst_description, src_description_full)
    # entry_exists takes a reference, so hand it the description path: a raw
    # dotted stem would be re-stemmed and the wrong entry checked.
    if not same_entry and entry_exists(dst_workspace, dst_description):
        raise HTTPException(status_code=409, detail="Destination already exists")
    for source, target in renames:
        if _is_blocked_by_other(dst_workspace / target, src_workspace / source):
            raise HTTPException(
                status_code=409,
                detail=f"Destination already exists: {_shown(dst_store, target)}",
            )

    for source, target in renames:
        target_path = dst_workspace / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        (src_workspace / source).rename(target_path)

    src_name = PurePosixPath(src_stem).name
    dst_name = PurePosixPath(dst_stem).name
    if moved_assets is not None and src_name != dst_name:
        # The markdown references its assets as ``<stem>.assets/...``; rewrite
        # those references when the stem's basename changed.
        description_full = dst_workspace / dst_description
        body = _decode_existing(description_full, _shown(dst_store, dst_description))
        updated = repoint_asset_refs(body.text, src_name, dst_name)
        description_full.write_text(updated, encoding="utf-8")

    # Move exactly this entry's row; a same-named sibling directory's rows
    # (stems below ``src_stem/``) belong to other documents and stay put.
    await db_documents.move_document(src_store, src_stem, dst_store, dst_stem)
    # The described/extracted asset children live under the ``.assets`` sibling
    # of ``src_stem`` — move their rows too so nothing stays searchable at a
    # path that no longer exists.
    if metadata.assets_dir:
        await db_documents.move_subtree(
            src_store, metadata.assets_dir, dst_store, assets_dir_for_stem(dst_stem)
        )


async def move_document(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> None:
    """Move a logical entry, its original, and its child-assets subtree.

    *src_store* and *dst_store* may be the same casebase (a rename within one
    workspace) or two different ones (migrating between the personal and a
    shared workspace).  The FS renames + SQL move run to completion under the
    lock(s) even on a cancel (:func:`shield_to_completion`) so a mid-move
    cancellation cannot leave the renamed files pointing at stale SQL rows.
    """
    async with _locked_for(src_store, src, dst_store=dst_store):
        await shield_to_completion(
            _move_document_locked(src_store, dst_store, src, dst)
        )
