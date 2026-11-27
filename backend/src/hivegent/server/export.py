"""One-way export of what a person owns, one ZIP archive of the parts they ask for.

The archive holds their documents as the files of their workspace, each
conversation as the archive its export button downloads beside the files of
its ``/tmp`` and its delegated tasks, their memory page, their skills as their
files, and their preferences.
It is built in full before the first byte is sent, so a failure is an
error, never a truncated archive.
"""

import asyncio
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from enum import StrEnum
from functools import partial
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from cbrkit.io import is_partial
from pydantic_ai.messages import ModelMessage, ModelRequest

from ..config import settings
from ..db.conversations import (
    OwnedConversation,
    load_conversation,
    load_conversation_summary,
    owned_conversations,
)
from ..db.memory import load_memory
from ..db.users import load_preferences
from ..delegations import TASKS_FOLDER
from ..shared.tree import tree
from ..shared.zips import build_zip, write_file
from ..skills import library
from ..store import User, UserScope
from ..tmp import TMP_FOLDER
from ..transparency import sign_provenance
from ..types import (
    ConversationArchive,
    InstructionsSnapshot,
    Preferences,
    ServerConversation,
)
from .turns import ui_id
from .vercel import dump_transcript

__all__ = [
    "ExportPart",
    "build_export",
    "conversation_archive",
    "export_filename",
    "instruction_snapshots",
]


class ExportPart(StrEnum):
    """A part of what a person owns, exported on its own or with the others."""

    DOCUMENTS = "documents"
    CONVERSATIONS = "conversations"
    MEMORY = "memory"
    SKILLS = "skills"
    PREFERENCES = "preferences"


type _Members = Mapping[str, Path | str]
"""The members of an archive by name, a file to copy or a text to write."""


def instruction_snapshots(
    messages: Sequence[ModelMessage],
) -> list[InstructionsSnapshot]:
    """Group *messages* by the system prompt each was sent under, by their UI message ids.

    pydantic-ai stamps the fully composed prompt (agent-level blocks plus every
    live capability's) onto each
    ``ModelRequest`` it sends, and that survives into the stored history, so the
    prompts are read back here rather than recomposed, since recomposing would
    describe today's settings, not the ones the turn actually ran under.
    Consecutive messages sharing a prompt collapse into one snapshot.
    """
    snapshots: list[InstructionsSnapshot] = []

    for message in messages:
        if (
            not isinstance(message, ModelRequest)
            or not message.instructions
            or (message_id := ui_id(message)) is None
        ):
            continue

        text = message.instructions

        if snapshots and snapshots[-1].text == text:
            if message_id not in snapshots[-1].message_ids:
                snapshots[-1].message_ids.append(message_id)
        else:
            snapshots.append(InstructionsSnapshot(message_ids=[message_id], text=text))

    return snapshots


async def conversation_archive(
    conversation: OwnedConversation,
) -> ConversationArchive | None:
    """The archive of *conversation*'s active history, ``None`` once it is gone.

    Its ``backend`` half carries the persisted messages and the system prompts
    they ran under, while ``frontend`` is left unset, since no browser state
    speaks for it here.
    """
    summary, loaded = await asyncio.gather(
        load_conversation_summary(conversation), load_conversation(conversation)
    )

    if summary is None or loaded is None:
        return None

    server = ServerConversation(
        id=str(summary.id),
        title=summary.title,
        messages=dump_transcript(loaded.messages, loaded.deferred_tool_requests),
        instructions=instruction_snapshots(loaded.messages),
    )

    # Signed metadata is the machine-readable marking a container format
    # carries (Code of Practice, Sub-measure 1.1.1).
    if settings.transparency is not None:
        server.provenance = sign_provenance(settings.transparency, server)

    return ConversationArchive(backend=server)


def _files(root: Path, prefix: str) -> dict[str, Path]:
    """The regular files below *root* by their path below *prefix*, partial writes and symlinks left out.  Blocking."""
    return {
        f"{prefix}/{path.relative_to(root).as_posix()}": path
        for path in sorted(tree(root))
        if not path.is_symlink() and path.is_file() and not is_partial(path.name)
    }


async def _conversations(user: User) -> dict[str, Path | str]:
    members: dict[str, Path | str] = {}

    # One at a time, so a long history never holds many connections of the pool.
    for conversation in await owned_conversations(user):
        archive = await conversation_archive(conversation)

        if archive is None:
            continue

        prefix = f"{ExportPart.CONVERSATIONS}/{conversation.id}"
        members[f"{prefix}.json"] = archive.model_dump_json(indent=2)
        folder = conversation.folder(settings.data_dir)

        for name in (TMP_FOLDER, TASKS_FOLDER):
            members |= await asyncio.to_thread(
                _files, folder / name, f"{prefix}/{name}"
            )

    return members


async def _members(user: User, part: ExportPart) -> _Members:
    scope = UserScope(user.id)

    match part:
        case ExportPart.DOCUMENTS:
            return await asyncio.to_thread(_files, user.home.path, part)
        case ExportPart.CONVERSATIONS:
            return await _conversations(user)
        case ExportPart.MEMORY:
            memory = await load_memory(user)

            return {} if memory is None else {"memory.md": memory}
        case ExportPart.SKILLS:
            return await asyncio.to_thread(_files, library(scope).path, part)
        case ExportPart.PREFERENCES:
            preferences = Preferences.stored(await load_preferences(user.id))

            return {"preferences.json": preferences.model_dump_json(indent=2)}


def _write(members: _Members, archive: ZipFile) -> None:
    for name, member in members.items():
        if isinstance(member, str):
            archive.writestr(name, member)
            continue

        # A file removed since it was listed, such as an evicted tool result, is no longer the user's.
        with suppress(FileNotFoundError):
            write_file(archive, member, name)


async def build_export(
    user: User, parts: Collection[ExportPart]
) -> SpooledTemporaryFile[bytes]:
    """The ZIP archive of the *parts* of what *user* owns, rewound for reading.

    Raises:
        AccessDenied: For a service principal, which owns nothing.
    """
    user.require_person()
    members: dict[str, Path | str] = {}

    for part in ExportPart:
        if part in parts:
            members |= await _members(user, part)

    return await asyncio.to_thread(build_zip, partial(_write, members), ZIP_DEFLATED)


def export_filename(parts: Collection[ExportPart]) -> str:
    """The name of the archive of *parts*, after the part when it is one."""
    match list(parts):
        case [part]:
            return f"hivegent-{part}.zip"
        case _:
            return "hivegent-export.zip"
