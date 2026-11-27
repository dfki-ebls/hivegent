"""Tests for the account and conversation cleanup routes."""

from contextlib import nullcontext
from pathlib import Path
from zipfile import ZipFile

import pytest
from exar.typing import Changes, Update
from fastapi import HTTPException

from hivegent import conversation_files
from hivegent.config import settings
from hivegent.db import memory
from hivegent.db.conversations import OwnedConversation
from hivegent.delegations import TASKS_FOLDER
from hivegent.server.export import ExportPart, ExportSources, build_export
from hivegent.server.routes import account
from hivegent.server.routes.conversations import history
from hivegent.skills import library
from hivegent.store import User, UserScope
from hivegent.tmp import clear_tmp, tmp_dir
from hivegent.types import ConversationArchive, MemoryPage
from tests.helpers import ConversationRows, label_id, owned


async def test_tmp_folders_go_by_the_conversation_ids_the_database_names(
    data_dir: Path, conversation_rows: ConversationRows
) -> None:
    """One conversation, clearing temporary files, and every one of them, folders before rows."""
    user = User(id="owner")
    folders = {cid: tmp_dir(data_dir, owned(cid, user)) for cid in ("c1", "c2", "c3")}

    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")

    await history.delete_conversation(owned("c1", user))
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c2", "c3"]

    # A conversation that never ran has no folder to count.
    cleared = await clear_tmp([owned("c2", user), owned("never-ran", user)])
    assert cleared == 1
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c3"]

    await history.delete_all_conversations_route(user)
    assert not any(folder.exists() for folder in folders.values())
    assert conversation_rows.removed == [(str(label_id("c1")), False), ("owner", False)]


async def test_a_folder_that_fails_to_go_keeps_its_row(
    monkeypatch: pytest.MonkeyPatch, conversation_rows: ConversationRows
) -> None:
    """So no later claim of the freed id reaches what the folder held."""

    def fail(_path: object) -> None:
        raise OSError

    monkeypatch.setattr(conversation_files, "remove_path", fail)

    with pytest.raises(OSError):
        await conversation_files.delete_conversation(owned("c1"))

    assert conversation_rows.removed == []


@pytest.mark.parametrize("stored", [False, True])
async def test_a_replaced_conversation_keeps_its_folder_until_stored(
    data_dir: Path, conversation_rows: ConversationRows, stored: bool
) -> None:
    """A failed import leaves the conversation whole, its files included."""
    folder = tmp_dir(data_dir, owned("c1"))
    folder.mkdir(parents=True)

    if not stored:
        conversation_rows.import_error = ValueError()

    with nullcontext() if stored else pytest.raises(ValueError):
        await conversation_files.replace_conversation(owned("c1"), [])

    assert conversation_rows.imported == [True]
    assert folder.exists() != stored


async def test_the_export_holds_the_parts_asked_for(data_dir: Path) -> None:
    """Each part on its own or all at once, without partial writes or symlinks."""
    user = User(id="owner")
    scope = UserScope(user.id)
    conversation = owned("c1", user)
    files = {
        user.home.path / "notes/report.md": "report",
        user.home.path / ".~0123456789abcdef.partial": "half",
        library(scope).path / "lint/SKILL.md": "skill",
        tmp_dir(data_dir, conversation) / "result.txt": "tmp",
        conversation.folder(data_dir) / TASKS_FOLDER / "child.json": "task history",
    }

    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    (user.home.path / "link.md").symlink_to(data_dir)

    async def conversations(_owner: User) -> list[OwnedConversation]:
        return [conversation, owned("gone", user)]

    async def archive(c: OwnedConversation) -> ConversationArchive | None:
        return ConversationArchive() if c == conversation else None

    async def memory(_owner: User) -> str:
        return "remembered"

    async def preferences(_owner: str) -> dict[str, str]:
        return {}

    sources = ExportSources(conversations, archive, memory, preferences)

    async def names(parts: frozenset[ExportPart]) -> list[str]:
        with (
            await build_export(user, parts, sources) as spool,
            ZipFile(spool) as zipped,
        ):
            return zipped.namelist()

    cid = conversation.id
    assert await names(frozenset(ExportPart)) == [
        "documents/notes/report.md",
        f"conversations/{cid}.json",
        f"conversations/{cid}/tmp/result.txt",
        f"conversations/{cid}/tasks/child.json",
        "memory.md",
        "skills/lint/SKILL.md",
        "preferences.json",
    ]
    assert await names(frozenset({ExportPart.MEMORY})) == ["memory.md"]


async def test_the_user_edits_their_memory_and_an_empty_page_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user writes their memory page whole, and shortens one over the limit."""
    user = User(id="owner")
    limit = settings.limits.max_memory_chars
    stored = "x" * (limit + 10)
    shorter = "y" * (limit + 5)
    written: list[Changes[str, str]] = []
    cleared: list[User] = []

    class Page:
        async def write(self, changes: Changes[str, str]) -> Changes[str, str]:
            written.append(changes)
            return changes

    async def clear(owner: User) -> bool:
        cleared.append(owner)
        return True

    async def load(_owner: User) -> str:
        return stored

    monkeypatch.setattr(memory, "user_memory", lambda _owner: Page())
    monkeypatch.setattr(memory, "clear_memory", clear)
    monkeypatch.setattr(account, "load_memory", load)

    assert (await account.get_memory(user)).content == stored
    page = MemoryPage(content=shorter)
    assert await account.put_memory(page, user) == page
    assert [item.value for item in written[0].items if isinstance(item, Update)] == [
        shorter
    ]

    with pytest.raises(HTTPException):
        _ = await account.put_memory(MemoryPage(content=stored + "!"), user)

    _ = await account.put_memory(MemoryPage(content="  "), user)
    assert cleared == [user]
