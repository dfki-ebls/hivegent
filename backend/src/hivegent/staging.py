"""Changesets a sandboxed program staged, kept until ``apply_changes`` takes them.

A program's changes are approved after the program ran, possibly after a page
reload or a server restart, so they live as one JSON file each in the
``changesets`` folder below the conversation's own
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) rather than in
memory.  Files rather than a table: a staged changeset is write-once, read-once
state with no query beyond its id, and one whose approval was denied or
abandoned is dropped when the next request answers it, so the sweep
(:func:`~hivegent.conversation_files.sweep_conversations`) only expires what was
never answered, and deleting the conversation deletes the rest with its folder.

What is stored is the program's own spelling, a
:class:`~hivegent.changes.Changeset` of canonical paths with their bases: the
paths are routed again with the approver's stores when it is applied, so
access revoked in between is honoured.  Every function naming a changeset takes
the :class:`~hivegent.db.conversations.OwnedConversation` it was staged in, so
neither another user nor another conversation can apply or discard one by
guessing its id.
"""

import asyncio
import re
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .changes import Changeset
from .config import settings
from .db.conversations import OwnedConversation
from .files import atomic_write, remove_path, rmdir_if_empty

__all__ = [
    "CHANGESETS_FOLDER",
    "discard_staged",
    "expire_staged",
    "load_staged",
    "stage",
]

CHANGESETS_FOLDER = "changesets"
"""The folder below a conversation's own that holds what it staged."""

_ID = re.compile(r"[0-9a-f]{32}")
_ADAPTER = TypeAdapter(Changeset[str])


def _folder(conversation: OwnedConversation) -> Path:
    return conversation.folder(settings.data_dir) / CHANGESETS_FOLDER


def _path(conversation: OwnedConversation, changeset_id: str) -> Path | None:
    """Where *conversation*'s *changeset_id* is stored, ``None`` for an id :func:`stage` never mints.

    The id arrives from a model's tool call, so only the exact shape
    :func:`stage` mints may become a file name, which no traversal can escape.
    """
    return _folder(conversation) / f"{changeset_id}.json" if _ID.fullmatch(changeset_id) else None


async def stage(conversation: OwnedConversation, changeset: Changeset[str]) -> str:
    """Store *changeset* for *conversation* and return the id that applies it."""
    changeset_id = uuid4().hex
    data = _ADAPTER.dump_json(changeset)
    await asyncio.to_thread(atomic_write, _folder(conversation) / f"{changeset_id}.json", data)

    return changeset_id


def _read(path: Path | None) -> Changeset[str] | None:
    try:
        return _ADAPTER.validate_json(path.read_bytes()) if path else None
    except (OSError, ValidationError):
        return None


async def load_staged(
    conversation: OwnedConversation, changeset_id: str
) -> Changeset[str] | None:
    """Return the changeset *conversation* staged under *changeset_id*, or ``None``.

    ``None`` alike for an id that never existed, one already applied or
    expired, and one another conversation staged, so a probe learns nothing.
    """
    return await asyncio.to_thread(_read, _path(conversation, changeset_id))


async def discard_staged(conversation: OwnedConversation, changeset_id: str) -> None:
    """Forget a changeset *conversation* staged, whether or not it is still there."""
    path = _path(conversation, changeset_id)

    if path is not None:
        await asyncio.to_thread(path.unlink, missing_ok=True)


def expire_staged(folder: Path, cutoff: float) -> None:
    """Remove what *cutoff* expired from a ``changesets`` *folder*, and the folder once empty.

    Tolerates a :func:`stage` running meanwhile: a file it renames away is
    already gone, and a folder it writes into again is no longer empty.  The
    partial files a crash left expire like any other.
    """
    if not folder.is_dir():
        return

    for path in folder.iterdir():
        with suppress(FileNotFoundError):
            if path.lstat().st_mtime < cutoff:
                remove_path(path)

    rmdir_if_empty(folder)
