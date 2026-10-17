"""What a conversation keeps on disk, and when it goes.

Each conversation owns one folder
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) holding its
``/tmp`` (:mod:`hivegent.tmp`) and the changesets its programs staged
(:mod:`hivegent.staging`), each module keeping its own content's rules.  No row
cascade reaches the folder, so this module is where it ends: deleting
conversations deletes their rows and folders together
(:func:`delete_conversation`, :func:`delete_conversations`), a reset deletes
every folder (:func:`reset_conversation_files`), and
:func:`sweep_conversations` removes a folder left idle, which also catches what
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
    remove_conversation,
)
from .files import remove_path, remove_paths, touched_since
from .store import User
from .tmp import TMP_FOLDER, tmp_root
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


def _expire(folder: Path, cutoff: float) -> bool:
    """Remove *folder* unless anything below it changed since *cutoff*, returning whether it went."""
    if touched_since(folder, cutoff):
        return False

    remove_path(folder)

    return True


async def sweep_conversations() -> int:
    """Remove every conversation folder untouched for ``conversations.ttl_hours``, returning how many went.

    One rule covers ``/tmp``, the changesets, and a folder naming no
    conversation row alike, judged by the newest change anywhere below the
    folder.  A folder a deletion took meanwhile counts as touched, and a
    new conversation's folder, whose row is written only when its first turn
    ends, is in use and so stays.  Each idle folder is judged again under the
    lock ``/tmp``'s writes hold before it goes.
    """
    root = OwnedConversation.root(settings.data_dir)

    if not root.is_dir():
        return 0

    cutoff = time.time() - settings.conversations.ttl_hours * 3600

    def due() -> list[Path]:
        return [
            f for f in root.iterdir() if f.is_dir() and not touched_since(f, cutoff)
        ]

    removed = 0

    for folder in await asyncio.to_thread(due):
        async with root_lock(tmp_root(folder / TMP_FOLDER)):
            removed += await asyncio.to_thread(_expire, folder, cutoff)

    return removed
