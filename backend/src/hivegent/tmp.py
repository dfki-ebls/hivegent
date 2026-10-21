"""A conversation's ``/tmp``: its working files, outside every workspace.

Each chat conversation owns one folder below its own
(:meth:`~hivegent.db.conversations.OwnedConversation.folder`), and every agent
surface spells it ``/tmp``: the read and write tools, a tool result too large to
show whole, and a sandboxed program, where it is mounted at ``/tmp``.  It lives
for the conversation like ``/tmp`` lives for a container session, and is never
indexed, searched, or announced.

Its changes commit through the one changeset gateway as a
:class:`~hivegent.workspace.operations.Folder`, written directly with no
approval, while this module keeps what is ``/tmp``'s own: its size cap and the
saved tool results only the host writes and the cap may evict.

There is no user level: conversation ids are server-generated keys and the
``conversations`` row names the one user a folder belongs to, so ownership is
read from the database and never from the path.  :func:`tmp_dir` takes only an
:class:`~hivegent.db.conversations.OwnedConversation`, and the folder ends with
its conversation's (:mod:`hivegent.conversation_files`).
"""

import asyncio
from collections.abc import Iterable
from pathlib import Path

from .changes import Changeset, Write
from .config import settings
from .db.conversations import OwnedConversation
from .files import remove_paths
from .tools.base import Direct, SearchPath, ToolRetry
from .tools.mutations import mutation_errors
from .tools.scope import PrefixScope
from .workspace import Gateway
from .workspace.operations import Folder, Quota

__all__ = [
    "TMP_FOLDER",
    "TMP_SCOPE",
    "clear_tmp",
    "save_result",
    "tmp_dir",
    "tmp_root",
    "tmp_search_path",
]

TMP_FOLDER = "tmp"
"""The folder below a conversation's own that ``/tmp`` names."""

TMP_SCOPE = PrefixScope("/tmp")
"""How every agent surface spells a conversation's folder."""

_RESULTS_DIR = ".tool-results"
"""The folder below ``/tmp`` that holds the tool results too large to show whole."""

_POLICY = Direct(reserved=_RESULTS_DIR)
"""Written directly, the saved results kept for the host."""


def tmp_dir(data_dir: Path, conversation: OwnedConversation) -> Path:
    """The folder ``/tmp`` names for *conversation*, without creating it.

    Only an :class:`OwnedConversation` names one, so no caller reaches the
    folder of a conversation its user does not own.
    """
    return conversation.folder(data_dir) / TMP_FOLDER


def tmp_search_path(data_dir: Path, conversation: OwnedConversation) -> SearchPath:
    """The ``/tmp`` root a conversation's run resolves against, written directly."""
    return SearchPath(
        path=tmp_dir(data_dir, conversation), scope=TMP_SCOPE, policy=_POLICY
    )


def tmp_root(folder: Path) -> Folder:
    """The gateway root that writes *folder* directly, within ``tmp.max_bytes``.

    The host saving a tool result may write the saved results and evict the
    oldest to make room, since a result can be asked for again while the
    run's own state cannot.
    """
    return Folder(
        folder, TMP_SCOPE, _POLICY, Quota(settings.tmp.max_bytes, _RESULTS_DIR)
    )


async def save_result(folder: Path, name: str, content: str) -> str:
    """Save a tool result as *name* among *folder*'s saved results, returning its canonical path.

    Committed through the gateway like any other ``/tmp`` write, as the host,
    so the cap applies after dropping the oldest saved results the new one
    would not fit beside.

    Raises:
        ToolRetry: When *name* is no single filename, or the result does not
            fit even beside no other saved one.
    """
    if name in {"", ".."} or Path(name).name != name:
        raise ToolRetry("A saved result must have a single filename.")

    target = TMP_SCOPE.render(f"{_RESULTS_DIR}/{name}")

    with mutation_errors(ToolRetry):
        _ = await Gateway((tmp_root(folder),), host=True).apply(
            Changeset((Write(target, content),))
        )

    return target


def _files(folder: Path) -> int:
    return sum(len(files) for _, _, files in folder.walk()) if folder.is_dir() else 0


async def clear_tmp(conversations: Iterable[OwnedConversation]) -> int:
    """Empty the ``/tmp`` of *conversations*, returning how many files it held.

    Their staged changesets are pending approvals rather than temporary files,
    so they stay.
    """
    folders = [
        tmp_dir(settings.data_dir, conversation) for conversation in conversations
    ]

    def clear() -> int:
        files = sum(map(_files, folders))
        remove_paths(folders)

        return files

    return await asyncio.to_thread(clear)
