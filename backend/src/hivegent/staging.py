"""Changesets a sandboxed program staged, kept until ``apply_changes`` takes them.

A program's changes are approved after the program ran, possibly after a page
reload or a server restart, so they live as one JSON file each in the
``changesets`` folder below the conversation's own
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) rather than in
memory.  Files rather than a table: a staged changeset is write-once, read-once
state with no query beyond its id.  Every saved turn keeps only those its
history still awaits an approval for or it staged itself (:func:`retain_staged`),
so one denied or abandoned goes with the next turn, while one still
awaited goes with its conversation, or once the sweep
(:func:`~hivegent.conversation_files.sweep_conversations`) finds the folder of
a conversation that no longer exists idle.
A file that exists but cannot be read raises and stays, so it is never taken
for one that was applied, while one that holds no changeset any more, such as
one a previous release staged, is dropped and answers as expired.

What is stored is the program's own spelling, a
:class:`~hivegent.tools.changes.Changeset` of canonical paths with their bases: the
paths are routed again with the approver's stores when it is applied, so
access revoked in between is honoured.  Every function naming a changeset takes
the :class:`~hivegent.db.conversations.OwnedConversation` it was staged in, so
neither another user nor another conversation can apply or discard one by
guessing its id.
"""

import asyncio
import logging
from collections.abc import Collection
from pathlib import Path
from uuid import uuid4

from cbrkit.io import atomic_write
from pydantic import TypeAdapter, ValidationError

from .config import parse_uuid, settings
from .db.conversations import OwnedConversation
from .files import rmdir_if_empty
from .tools.changes import Changeset

logger = logging.getLogger(__name__)

__all__ = [
    "CHANGESETS_FOLDER",
    "discard_staged",
    "load_staged",
    "retain_staged",
    "stage",
]

CHANGESETS_FOLDER = "changesets"
"""The folder below a conversation's own that holds what it staged."""

_ADAPTER = TypeAdapter(Changeset[str])


def _folder(conversation: OwnedConversation) -> Path:
    return conversation.folder(settings.data_dir) / CHANGESETS_FOLDER


def _path(conversation: OwnedConversation, changeset_id: str) -> Path | None:
    """Where *conversation*'s *changeset_id* is stored, ``None`` for an id :func:`stage` never mints.

    The id arrives from a model's tool call, so only the canonical spelling
    of a UUID, the shape :func:`stage` mints, may become a file name, which no
    traversal can escape.
    """
    parsed = parse_uuid(changeset_id)

    return None if parsed is None else _folder(conversation) / f"{parsed}.json"


async def stage(conversation: OwnedConversation, changeset: Changeset[str]) -> str:
    """Store *changeset* for *conversation* and return the id that applies it."""
    changeset_id = str(uuid4())
    data = _ADAPTER.dump_json(changeset)
    await asyncio.to_thread(
        atomic_write, _folder(conversation) / f"{changeset_id}.json", data
    )

    return changeset_id


def _read(path: Path | None) -> Changeset[str] | None:
    if path is None:
        return None

    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None

    try:
        return _ADAPTER.validate_json(data)
    except ValidationError:
        logger.warning(
            "Dropping the unreadable staged changeset %s", path, exc_info=True
        )
        path.unlink(missing_ok=True)

        return None


async def load_staged(
    conversation: OwnedConversation, changeset_id: str
) -> Changeset[str] | None:
    """Return the changeset *conversation* staged under *changeset_id*, or ``None``.

    ``None`` alike for an id that never existed, one already applied or
    discarded, one whose file holds no changeset, and one another conversation
    staged, so a probe learns nothing.

    Raises:
        OSError: When its file exists but cannot be read.
    """
    return await asyncio.to_thread(_read, _path(conversation, changeset_id))


async def discard_staged(conversation: OwnedConversation, changeset_id: str) -> None:
    """Forget a changeset *conversation* staged, whether or not it is still there."""
    path = _path(conversation, changeset_id)

    if path is not None:
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _retain(folder: Path, keep: frozenset[str], since: float) -> None:
    try:
        staged = list(folder.iterdir())
    except FileNotFoundError:
        return

    for path in staged:
        try:
            if path.name not in keep and path.stat().st_mtime < since:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    rmdir_if_empty(folder)


async def retain_staged(
    conversation: OwnedConversation, changeset_ids: Collection[str], since: float
) -> None:
    """Delete every changeset *conversation* staged before *since* except *changeset_ids*.

    *since* is the epoch time a turn started at, so what it staged stays for
    the user to approve in the next one.

    Its folder goes once nothing is left, so the sweep can remove an idle
    conversation folder whole.
    """
    keep = frozenset(
        f"{parsed}.json"
        for changeset_id in changeset_ids
        if (parsed := parse_uuid(changeset_id)) is not None
    )
    await asyncio.to_thread(_retain, _folder(conversation), keep, since)
