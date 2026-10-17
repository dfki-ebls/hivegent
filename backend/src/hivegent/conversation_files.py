"""What a conversation keeps on disk, and when it goes.

Each conversation owns one folder
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) holding its
``/tmp`` (:mod:`hivegent.tmp`) and the changesets its programs staged
(:mod:`hivegent.staging`), each module keeping its own content's rules.  No row
cascade reaches the folder, so this module is where it ends: deleting
conversations deletes their rows and folders together
(:func:`delete_conversation`, :func:`delete_conversations`), a reset deletes
every folder (:func:`reset_conversation_files`), and
:func:`sweep_conversations` expires what outlived its lifetime and catches what
those cannot see, a crash between a row and its folder or a first turn that
never persisted its row.
"""

import asyncio
import time
from collections.abc import Iterable
from pathlib import Path

from .config import settings
from .db.conversations import (
    OwnedConversation,
    delete_all_conversations,
    existing_conversation_ids,
    remove_conversation,
)
from .files import remove_path, remove_paths, rmdir_if_empty, touched_since
from .staging import CHANGESETS_FOLDER, expire_staged
from .store import User
from .tmp import TMP_FOLDER, expire_tmp, tmp_expired, tmp_root
from .workspace.locks import root_lock

__all__ = [
    "delete_conversation",
    "delete_conversations",
    "reset_conversation_files",
    "sweep_conversations",
]


async def _remove_folders(conversations: Iterable[OwnedConversation]) -> None:
    folders = [conversation.folder(settings.data_dir) for conversation in conversations]

    await asyncio.to_thread(remove_paths, folders)


async def delete_conversation(conversation: OwnedConversation) -> bool:
    """Delete *conversation*'s row and folder, returning whether it had a row."""
    if not await remove_conversation(conversation):
        return False

    await _remove_folders([conversation])

    return True


async def delete_conversations(user: User) -> None:
    """Delete every conversation *user* owns, rows and folders alike."""
    await _remove_folders(await delete_all_conversations(user))


async def reset_conversation_files() -> None:
    """Delete every conversation's folder, as a reset of the conversations does."""
    await asyncio.to_thread(remove_path, OwnedConversation.root(settings.data_dir))


def _expire(folder: Path, *, orphaned: float | None, stale_tmp: float) -> bool:
    """Remove what *folder* outlived under its lock, returning whether all of it went.

    A folder naming no row goes whole once untouched since *orphaned*, and
    otherwise only its expired ``/tmp``, the folder itself once that left it
    empty.
    """
    if orphaned is not None and not touched_since(folder, orphaned):
        remove_path(folder)
    else:
        expire_tmp(folder, stale_tmp)
        rmdir_if_empty(folder)

    return not folder.exists()


async def sweep_conversations() -> int:
    """Expire what every conversation's folder outlived, returning how many folders went.

    A ``/tmp`` untouched for ``tmp.ttl_hours`` and a changeset unanswered for
    ``sandbox.staged_changeset_ttl_hours`` expire on their own.  A folder naming
    no conversation row goes whole, but only once it sat untouched for a sweep
    interval: a new conversation's row is written when its first turn ends, so
    a folder that turn is still using has none yet.

    One query answers which folders have a row, and changesets expire without
    a lock, since staging one tolerates the sweep, so only a folder with
    something of ``/tmp``'s to remove waits on the lock its writes hold.
    """
    root = OwnedConversation.root(settings.data_dir)

    if not root.is_dir():
        return 0

    now = time.time()
    orphaned = now - settings.tmp.sweep_interval_hours * 3600
    stale_tmp = now - settings.tmp.ttl_hours * 3600
    stale_staged = now - settings.sandbox.staged_changeset_ttl_hours * 3600

    folders = await asyncio.to_thread(lambda: [f for f in root.iterdir() if f.is_dir()])
    live = await existing_conversation_ids([folder.name for folder in folders])

    def due() -> list[Path]:
        picked: list[Path] = []

        for folder in folders:
            # One a deletion took meanwhile has nothing left to expire.
            if not folder.is_dir():
                continue

            expire_staged(folder / CHANGESETS_FOLDER, stale_staged)

            if (
                folder.name not in live
                or tmp_expired(folder, stale_tmp)
                or not any(folder.iterdir())
            ):
                picked.append(folder)

        return picked

    removed = 0

    for folder in await asyncio.to_thread(due):
        async with root_lock(tmp_root(folder / TMP_FOLDER)):
            removed += await asyncio.to_thread(
                _expire,
                folder,
                orphaned=None if folder.name in live else orphaned,
                stale_tmp=stale_tmp,
            )

    return removed
