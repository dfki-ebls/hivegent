"""What a conversation keeps on disk, and when it goes.

Each conversation owns one folder
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) holding its
``/tmp`` (:mod:`hivegent.tmp`) and the changesets its programs staged
(:mod:`hivegent.staging`), each module keeping its own content's rules.  No row
cascade reaches the folder, so this module is where it ends: deleting
conversations stops their turns and deletes their folders and then their rows
(:func:`delete_conversation`, :func:`delete_conversations`), a reset deletes
every folder (:func:`reset_conversation_files`), and
:func:`sweep_conversations` removes a folder left idle, which also catches what
those cannot see, such as a run still writing after its conversation was
deleted.
Folders lie below their user's, so what such a run writes stays its user's.
"""

import asyncio
import time
from pathlib import Path

from .config import settings
from .db.conversations import (
    OwnedConversation,
    remove_conversation,
    remove_owned_conversations,
)
from .files import remove_path, rmdir_if_empty, touched_since
from .runs import runs
from .store import User
from .tmp import TMP_FOLDER, tmp_root
from .workspace.locks import root_lock

__all__ = [
    "delete_conversation",
    "delete_conversations",
    "reset_conversation_files",
    "sweep_conversations",
]


# A turn stops first, since it still writes to the history and the folder.
# Folders go before rows, so a folder that fails to go raises before its row
# does, its owner keeps the conversation, and no later claim of a freed id
# reaches what it held.


async def delete_conversation(conversation: OwnedConversation) -> bool:
    """Stop *conversation*'s turn and delete its folder and row, returning whether it had a row."""
    await runs.cancel(conversation)
    await asyncio.to_thread(remove_path, conversation.folder(settings.data_dir))

    return await remove_conversation(conversation)


async def delete_conversations(user: User) -> None:
    """Stop *user*'s turns and delete every conversation they own, their folders first."""
    await runs.cancel_all(user)
    await asyncio.to_thread(
        remove_path, OwnedConversation.user_root(settings.data_dir, user.id)
    )
    await remove_owned_conversations(user)


async def reset_conversation_files() -> None:
    """Stop every turn and delete every conversation's folder, as a reset of the conversations does."""
    await runs.aclose()
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
    folder.  A folder a deletion took meanwhile counts as touched.  Each idle
    folder is judged again under the lock ``/tmp``'s writes hold before it
    goes.
    """
    root = OwnedConversation.root(settings.data_dir)

    if not root.is_dir():
        return 0

    cutoff = time.time() - settings.conversations.ttl_hours * 3600

    def due() -> list[Path]:
        return [
            f for f in root.glob("*/*") if f.is_dir() and not touched_since(f, cutoff)
        ]

    def prune() -> None:
        for user in root.iterdir():
            rmdir_if_empty(user)

    removed = 0

    for folder in await asyncio.to_thread(due):
        async with root_lock(tmp_root(folder / TMP_FOLDER)):
            removed += await asyncio.to_thread(_expire, folder, cutoff)

    # A user without conversation folders leaves no empty one behind.
    await asyncio.to_thread(prune)

    return removed
