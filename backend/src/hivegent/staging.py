"""Changesets a sandboxed program staged, kept until ``apply_changes`` takes them.

A program's changes are approved after the program ran, possibly after a page
reload or a server restart, so they live as one JSON file each under
``<data_dir>/changesets/<owner>/`` rather than in memory.  Files rather than a
table: a staged changeset is write-once, read-once state with no query beyond
its id, and one whose approval was denied or abandoned is dropped when the next
request answers it, so the periodic prune only catches what was never answered.

What is stored is the program's own spelling, a
:class:`~hivegent.changes.Changeset` of canonical paths with their bases: the
paths are routed again with the approver's stores when it is applied, so
access revoked in between is honoured.  The owner is a path segment, so one
user can never apply or discard another's changes by guessing an id.
"""

import asyncio
import re
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .changes import Changeset
from .config import sanitize_user_id, settings
from .files import atomic_write, remove_path

__all__ = [
    "discard_staged",
    "load_staged",
    "prune_staged",
    "stage",
]

_ID = re.compile(r"[0-9a-f]{32}")
_ADAPTER = TypeAdapter(Changeset[str])


def _directory() -> Path:
    return settings.data_dir / "changesets"


def _file(owner: str, changeset_id: str) -> Path:
    return _directory() / sanitize_user_id(owner) / f"{changeset_id}.json"


def _path(owner: str, changeset_id: str) -> Path | None:
    """Where *owner*'s *changeset_id* is stored, ``None`` for an id :func:`stage` never mints.

    The id arrives from a model's tool call, so only the exact shape
    :func:`stage` mints may become a file name, which no traversal can escape.
    """
    return _file(owner, changeset_id) if _ID.fullmatch(changeset_id) else None


async def stage(owner: str, changeset: Changeset[str]) -> str:
    """Store *changeset* for *owner* and return the id that applies it."""
    changeset_id = uuid4().hex
    data = _ADAPTER.dump_json(changeset)
    await asyncio.to_thread(atomic_write, _file(owner, changeset_id), data)

    return changeset_id


def _read(path: Path | None) -> Changeset[str] | None:
    try:
        return _ADAPTER.validate_json(path.read_bytes()) if path else None
    except (OSError, ValidationError):
        return None


async def load_staged(owner: str, changeset_id: str) -> Changeset[str] | None:
    """Return the changeset *owner* staged under *changeset_id*, or ``None``.

    ``None`` alike for an id that never existed, one already applied or
    expired, and one another user staged, so a probe learns nothing.
    """
    return await asyncio.to_thread(_read, _path(owner, changeset_id))


async def discard_staged(owner: str, changeset_id: str) -> None:
    """Forget a changeset *owner* staged, whether or not it is still there."""
    path = _path(owner, changeset_id)

    if path is not None:
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _prune(directory: Path, cutoff: float) -> None:
    """Remove what *cutoff* expired, tolerating a :func:`stage` running meanwhile.

    A file it renames away is already gone, and a directory it writes into
    again is no longer empty, so neither fails the prune of the others.
    """
    if not directory.is_dir():
        return

    for owner in directory.iterdir():
        for path in owner.iterdir() if owner.is_dir() else (owner,):
            with suppress(FileNotFoundError):
                if path.lstat().st_mtime < cutoff:
                    remove_path(path)

        if owner.is_dir() and not any(owner.iterdir()):
            with suppress(OSError):
                owner.rmdir()


async def prune_staged() -> None:
    """Drop changesets, and partial files a crash left, older than the configured lifetime."""
    ttl = settings.sandbox.staged_changeset_ttl_hours * 3600
    await asyncio.to_thread(_prune, _directory(), time.time() - ttl)
