"""Text-mutation building blocks and the in-place rechunk.

The pieces :mod:`~hivegent.workspace.changeset` composes a write or an edit
from: the gate on what may be written as text at all, the optimistic
concurrency check against what the caller last saw, and the pure functions
deriving a document's new content from its current one.  None of them takes
a lock or writes anything, and :func:`rechunk` is the one public operation
left here, since it changes the index and never the workspace.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from fastapi import HTTPException

from ..changes import Basis, TextEdit, WriteMode
from ..chunkers.base import DocumentMetadata
from ..concurrency import shield_to_completion
from ..config import content_hash, settings
from ..converters import BINARY_WRITE_REASON, vision_media_type
from ..entries import ContentStat
from ..humanize import pluralize
from ..l10n import Localized
from ..store import Casebase
from ..text import DecodedText, read_text_file
from ..types import PipelineSpec
from .indexing import chunk_and_index_document
from .locks import _locked_for
from .paths import _shown, document_not_found, not_text

__all__ = ["TextMutation", "derive_text", "edit_mutation", "rechunk", "write_mutation"]


def _is_directory(path: str) -> Localized[str]:
    return Localized(en=f"'{path}' is a directory", de=f"„{path}“ ist ein Ordner")


def _binary_media(path: str, media_type: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' is a {media_type} binary and cannot be written as "
            "text; upload a replacement instead"
        ),
        de=(
            f"„{path}“ ist eine Binärdatei vom Typ {media_type} und kann nicht als "
            "Text geschrieben werden. Lade stattdessen einen Ersatz hoch"
        ),
    )


def _transcoded(encoding: str) -> Localized[str]:
    return Localized(
        en=f" The file was transcoded from {encoding} to UTF-8.",
        de=f" Die Datei wurde von {encoding} nach UTF-8 umkodiert.",
    )


def _hash_unread(path: str, expected: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' does not exist, so it could not have been read "
            f"(expected hash {expected}); omit expected_hash to create it"
        ),
        de=(
            f"„{path}“ existiert nicht und kann daher nicht gelesen worden sein "
            f"(erwarteter Hash {expected}). Lass expected_hash weg, um die Datei "
            "zu erstellen"
        ),
    )


def _hash_changed(path: str, expected: str, actual: str) -> Localized[str]:
    return Localized(
        en=(
            f"'{path}' changed since it was read "
            f"(expected hash {expected}, found {actual}); "
            "re-read it and retry with the new hash"
        ),
        de=(
            f"„{path}“ wurde seit dem Lesen geändert "
            f"(erwarteter Hash {expected}, gefunden {actual}). "
            "Lies die Datei erneut und versuche es mit dem neuen Hash noch einmal"
        ),
    )


def _old_string_missing(path: str) -> Localized[str]:
    return Localized(
        en=f"old_string not found in '{path}'",
        de=f"old_string wurde in „{path}“ nicht gefunden",
    )


def _old_string_ambiguous(path: str, count: int) -> Localized[str]:
    return Localized(
        en=(
            f"old_string appears {count} times in '{path}'; "
            "must be unique or call with replace_all=True"
        ),
        de=(
            f"old_string kommt {count}-mal in „{path}“ vor. Wähle einen eindeutigen "
            "Wert oder rufe das Tool mit replace_all=True auf"
        ),
    )


def _replaced(path: str, count: int) -> Localized[str]:
    return Localized(
        en=f"Replaced {count} {pluralize(count, 'occurrence')} in '{path}'.",
        de=f"{count} Vorkommen in „{path}“ ersetzt.",
    )


def _edits_applied(path: str, edits: int, count: int) -> Localized[str]:
    return Localized(
        en=(
            f"Applied {edits} edits with {count} "
            f"{pluralize(count, 'replacement')} to '{path}'."
        ),
        de=f"{edits} Änderungen mit {count} Ersetzungen in „{path}“ angewendet.",
    )


def _edit_failed(number: int, total: int, detail: str) -> Localized[str]:
    return Localized(
        en=f"Edit {number} of {total}: {detail}",
        de=f"Änderung {number} von {total}: {detail}",
    )


def _already_exists(path: str) -> Localized[str]:
    return Localized(en=f"'{path}' already exists", de=f"„{path}“ existiert bereits")


def _missing_for_mode(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' does not exist (use mode='replace' to create)",
        de=(
            f"„{path}“ existiert nicht (verwende mode='replace', um die Datei zu erstellen)"
        ),
    )


def _wrote(path: str, length: int) -> Localized[str]:
    return Localized(
        en=f"Wrote {length} characters to '{path}'.",
        de=f"{length} Zeichen in „{path}“ geschrieben.",
    )


def _created(path: str, length: int) -> Localized[str]:
    return Localized(
        en=f"Created '{path}' with {length} characters.",
        de=f"„{path}“ mit {length} Zeichen erstellt.",
    )


def _appended(path: str, length: int) -> Localized[str]:
    return Localized(
        en=f"Appended {length} characters to '{path}'.",
        de=f"{length} Zeichen an „{path}“ angehängt.",
    )


def _prepended(path: str, length: int) -> Localized[str]:
    return Localized(
        en=f"Prepended {length} characters to '{path}'.",
        de=f"{length} Zeichen am Anfang von „{path}“ eingefügt.",
    )


def _binary_write(path: str) -> Localized[str]:
    return Localized(
        en=f"'{path}' {BINARY_WRITE_REASON}",
        de=(
            f"„{path}“ ist ein Binärformat, das ein Textschreibvorgang nicht "
            "erstellen kann. Schreibe den Text stattdessen in einen „.md“-Pfad "
            "oder lade die Datei hoch"
        ),
    )


def _regenerated(report: str, path: str) -> Localized[str]:
    return Localized(
        en=(
            f"{report} Its searchable markdown '{path}' was "
            f"regenerated from the new content."
        ),
        de=(
            f"{report} Das durchsuchbare Markdown „{path}“ wurde aus dem neuen "
            "Inhalt neu erzeugt."
        ),
    )


def _decode_existing(file_path: Path, shown: str) -> DecodedText:
    """Decode an existing workspace file, rejecting content that is not text.

    Reads go through the shared decoder so a legacy-encoded file is editable
    and hashes the same way the read tools hash it; the rewrite is UTF-8, which
    normalises the file on its first edit.
    """
    decoded = read_text_file(file_path)
    if decoded is None:
        raise HTTPException(status_code=422, detail=not_text(shown).current)
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
        raise HTTPException(status_code=409, detail=_is_directory(shown).current)
    if (media_type := vision_media_type(file_path.name)) is not None:
        raise HTTPException(
            status_code=422,
            detail=_binary_media(shown, media_type).current,
        )
    return _decode_existing(file_path, shown) if file_path.is_file() else None


def _transcode_note(source_encoding: str | None) -> str:
    """Report the UTF-8 normalization of legacy-encoded existing content."""
    if source_encoding is None:
        return ""

    return _transcoded(source_encoding).current


def _fingerprint(basis: Basis) -> str:
    """Render a basis the way a refusal names it."""
    if isinstance(basis, str):
        return basis

    return f"mtime {basis.mtime_ns}, size {basis.size}, inode {basis.inode}"


def _check_basis(
    shown: str, file_path: Path | None, basis: Basis | None, current: str | None = None
) -> None:
    """Reject a change unless *file_path* still matches *basis*.

    The optimistic-concurrency guard: a basis comes from an earlier read, so a
    mismatch means the file moved on since, and a missing file (or none at
    all, for a change that replaces nothing) means the caller never read it.
    Both are a 409.  A content hash is compared against *current*, the
    decoded text the caller already holds, or the file's own text when it
    holds none.
    """
    if basis is None:
        return

    if file_path is None or not file_path.is_file():
        raise HTTPException(
            status_code=409, detail=_hash_unread(shown, _fingerprint(basis)).current
        )

    if isinstance(basis, ContentStat):
        actual = ContentStat.from_path(file_path)
        found = _fingerprint(actual) if actual is not None else "nothing"
        matches = actual == basis
    else:
        if current is None and (decoded := read_text_file(file_path)) is not None:
            current = decoded.text

        found = content_hash(current) if current is not None else "binary content"
        matches = found == basis

    if not matches:
        raise HTTPException(
            status_code=409,
            detail=_hash_changed(shown, _fingerprint(basis), found).current,
        )


type TextMutation = Callable[[str | None], tuple[str, str]]
"""Derive a document's new content and its report from its current content.

The current content is ``None`` for a document that does not exist yet.  Being
a plain function is what lets a change derive the same content again under the
lock and tell whether the document moved on while it was being prepared.
"""


def _replace(shown: str, text: str, edit: TextEdit) -> tuple[str, int]:
    """Apply one exact-string replacement, returning the text and its count."""
    count = text.count(edit.old_string)

    if count == 0:
        raise HTTPException(status_code=422, detail=_old_string_missing(shown).current)

    if count > 1 and not edit.replace_all:
        raise HTTPException(
            status_code=422, detail=_old_string_ambiguous(shown, count).current
        )

    if edit.replace_all:
        return text.replace(edit.old_string, edit.new_string), count

    return text.replace(edit.old_string, edit.new_string, 1), 1


def edit_mutation(shown: str, edits: Sequence[TextEdit]) -> TextMutation:
    """Build the in-order replacements behind an edit, landing as one write.

    Each replacement sees the text the previous one left, and a refusal names
    the edit it came from when there is more than one, so the caller can tell
    which to fix.
    """

    def mutate(current: str | None) -> tuple[str, str]:
        if current is None:
            raise HTTPException(
                status_code=404, detail=document_not_found(shown).current
            )

        text, total = current, 0

        for number, edit in enumerate(edits, start=1):
            try:
                text, count = _replace(shown, text, edit)
            except HTTPException as exc:
                if len(edits) == 1:
                    raise

                detail = _edit_failed(number, len(edits), exc.detail).current
                raise HTTPException(exc.status_code, detail) from exc

            total += count

        if len(edits) == 1:
            return text, _replaced(shown, total).current

        return text, _edits_applied(shown, len(edits), total).current

    return mutate


def write_mutation(shown: str, content: str, mode: WriteMode) -> TextMutation:
    """Build the write-mode composition behind a write."""

    def mutate(current: str | None) -> tuple[str, str]:
        if mode == "replace":
            return content, _wrote(shown, len(content)).current
        if mode == "create":
            if current is not None:
                raise HTTPException(
                    status_code=409, detail=_already_exists(shown).current
                )
            return content, _created(shown, len(content)).current
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=_missing_for_mode(shown).current,
            )
        if mode == "append":
            return current + content, _appended(shown, len(content)).current
        return content + current, _prepended(shown, len(content)).current

    return mutate


def derive_text(
    source: Path | None, shown: str, mutate: TextMutation, basis: Basis | None
) -> tuple[str | None, str, str]:
    """Derive a file's new text from what lies at *source*, ``None`` for nothing.

    The half every text change shares, the gateway's and a direct ``/tmp``
    write's alike: the current file must be text and still match *basis*.
    Whatever else the caller checks comes after, so an operation that needs
    the file to exist says so rather than being answered with a rule about
    creating one.

    Returns:
        The current text, the new text, and the report, which notes a transcode.
    """
    decoded = None if source is None else _editable_text(source, shown)
    current = None if decoded is None else decoded.text
    _check_basis(shown, source, basis, current)
    content, report = mutate(current)
    note = _transcode_note(decoded.source_encoding if decoded else None)

    return current, content, report + note


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
        shown = _shown(store, safe)
        decoded = _editable_text(file_path, shown)
        if decoded is None:
            raise HTTPException(
                status_code=404, detail=document_not_found(shown).current
            )
        return await shield_to_completion(
            chunk_and_index_document(
                store,
                safe,
                decoded.text,
                spec.chunking,
                stat=ContentStat.from_path(file_path),
            )
        )
