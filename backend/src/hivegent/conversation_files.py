"""What a conversation keeps on disk, and when it goes.

Each conversation owns one folder
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`) holding its
``/tmp`` (:mod:`hivegent.tmp`) and the changesets its programs staged
(:mod:`hivegent.staging`), each module keeping its own content's rules.  No row
cascade reaches the folder, so this module is where it ends: deleting
conversations stops their turns and deletes their folders and then their rows
(:func:`delete_conversation`, :func:`delete_conversations`), each stopping
its delegated tasks too (:mod:`hivegent.delegations`), replacing one
deletes its folder once its new history is stored (:func:`replace_conversation`),
a reset deletes every folder (:func:`reset_conversation_files`), and
:func:`sweep_conversations` removes an idle ``/tmp``, and the whole idle folder
of a conversation that no longer exists, which also catches what those cannot
see, such as a run still writing after its conversation was deleted.
Folders lie below their user's, so what such a run writes stays its user's.
"""

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID

from pydantic_ai.messages import ModelMessage

from .config import parse_uuid, settings
from .db.conversations import (
    ConversationSummary,
    OwnedConversation,
    conversation_owners,
    import_conversation,
    remove_conversation,
    remove_owned_conversations,
)
from .delegations import delegations
from .files import remove_path, rmdir_if_empty, touched_since
from .runs import runs
from .store import User
from .tmp import TMP_FOLDER, tmp_root
from .workspace.locks import root_lock

__all__ = [
    "delete_conversation",
    "delete_conversations",
    "replace_conversation",
    "reset_conversation_files",
    "sweep_conversations",
]


# A turn stops first, since it still writes to the history and the folder.
# Folders go before rows, so a folder that fails to go raises before its row
# does, its owner keeps the conversation, and no later claim of a freed id
# reaches what it held.


async def delete_conversation(conversation: OwnedConversation) -> bool:
    """Stop *conversation*'s turn and tasks and delete its folder and row, returning whether it had a row."""
    with delegations.suspend_wakeups(conversation):
        await runs.cancel(conversation)
        await delegations.close(conversation)
        await asyncio.to_thread(remove_path, conversation.folder(settings.data_dir))

        return await remove_conversation(conversation)


async def delete_conversations(user: User) -> None:
    """Stop *user*'s turns and tasks and delete every conversation they own, their folders first."""
    with delegations.suspend_wakeups(user.id):
        await runs.cancel_all(user)
        await delegations.close_all(user)
        await asyncio.to_thread(
            remove_path, OwnedConversation.user_root(settings.data_dir, user.id)
        )
        await remove_owned_conversations(user)


async def replace_conversation(
    conversation: OwnedConversation,
    messages: Sequence[ModelMessage],
    *,
    title: str | None = None,
) -> ConversationSummary | None:
    """Stop *conversation*'s turn and import *messages* as its whole history (:func:`import_conversation`).

    The folder of what it replaced goes only once the import committed, so a
    failed import leaves the conversation whole.
    """
    with delegations.suspend_wakeups(conversation):
        await runs.cancel(conversation)
        await delegations.close(conversation)
        summary = await import_conversation(conversation, messages, title=title)

        if summary is not None:
            await asyncio.to_thread(remove_path, conversation.folder(settings.data_dir))

    return summary


async def reset_conversation_files() -> None:
    """Stop every turn and task and delete every conversation's folder, as a reset of the conversations does."""
    with delegations.suspend_wakeups():
        await runs.aclose()
        await delegations.aclose()
        await asyncio.to_thread(remove_path, OwnedConversation.root(settings.data_dir))


def _expire(path: Path, cutoff: float) -> bool:
    """Remove *path* unless anything below it changed since *cutoff*, returning whether it went.

    Its parent goes too once that is left empty.
    """
    if touched_since(path, cutoff):
        return False

    remove_path(path)
    rmdir_if_empty(path.parent)

    return True


def _sweepable(folder: Path, owners: Mapping[UUID, str]) -> Path:
    """What of the idle *folder* may go: its ``/tmp`` while its conversation exists, else all of it.

    A changeset staged for approval goes only when answered or with its
    conversation, never silently while the conversation can still approve it.
    *owners* maps the existing conversations to their owners.
    """
    conversation_id = parse_uuid(folder.name)

    if (
        conversation_id is not None
        and owners.get(conversation_id) == folder.parent.name
    ):
        return folder / TMP_FOLDER

    return folder


async def sweep_conversations() -> int:
    """Remove what conversation folders hold untouched for ``conversations.ttl_hours``, returning how many went.

    A folder is idle when nothing anywhere below it changed, and a folder a
    deletion took meanwhile counts as touched.  An idle folder whose
    conversation exists loses its ``/tmp`` alone, while one naming no
    conversation goes whole (:func:`_sweepable`).  Each is judged again under
    the lock ``/tmp``'s writes hold before it goes.
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

    folders = await asyncio.to_thread(due)
    owners = await conversation_owners(
        {key for folder in folders if (key := parse_uuid(folder.name)) is not None}
    )

    for folder in folders:
        target = _sweepable(folder, owners)

        if not await asyncio.to_thread(target.exists):
            continue

        async with root_lock(tmp_root(folder / TMP_FOLDER)):
            removed += await asyncio.to_thread(_expire, target, cutoff)

    # A user without conversation folders leaves no empty one behind.
    await asyncio.to_thread(prune)

    return removed
