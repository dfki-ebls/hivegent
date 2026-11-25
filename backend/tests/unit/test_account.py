"""Tests for the account and conversation cleanup routes."""

from contextlib import nullcontext
from pathlib import Path
from uuid import UUID

import pytest

from hivegent import conversation_files
from hivegent.db.conversations import OwnedConversation
from hivegent.server.routes import account
from hivegent.server.routes.conversations import history
from hivegent.store import User
from hivegent.tmp import tmp_dir
from tests.helpers import label_id, owned


async def test_tmp_folders_go_by_the_conversation_ids_the_database_names(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One conversation, clearing temporary files, and every one of them, folders before rows."""
    user = User(id="owner")
    folders = {cid: tmp_dir(data_dir, owned(cid, user)) for cid in ("c1", "c2", "c3")}

    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")

    removed: list[UUID | str] = []

    async def remove_row(conversation: OwnedConversation) -> bool:
        assert not folders["c1"].exists()
        removed.append(conversation.id)

        return True

    async def remove_rows(owner: User) -> None:
        assert not any(folder.exists() for folder in folders.values())
        removed.append(owner.id)

    async def remaining(owner: User) -> list[OwnedConversation]:
        return [owned("c2", owner), owned("never-ran", owner)]

    monkeypatch.setattr(conversation_files, "remove_conversation", remove_row)
    monkeypatch.setattr(conversation_files, "remove_owned_conversations", remove_rows)
    monkeypatch.setattr(account, "owned_conversations", remaining)

    await history.delete_conversation(owned("c1", user))
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c2", "c3"]

    assert (await account.delete_tmp(user)).files_removed == 1
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c3"]

    await history.delete_all_conversations_route(user)
    assert not any(folder.exists() for folder in folders.values())
    assert removed == [label_id("c1"), "owner"]


async def test_a_folder_that_fails_to_go_keeps_its_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So no later claim of the freed id reaches what the folder held."""

    def fail(_path: object) -> None:
        raise OSError

    async def remove_row(_owned: object) -> bool:
        pytest.fail("the row went before its folder")

    monkeypatch.setattr(conversation_files, "remove_path", fail)
    monkeypatch.setattr(conversation_files, "remove_conversation", remove_row)

    with pytest.raises(OSError):
        await conversation_files.delete_conversation(owned("c1"))


@pytest.mark.parametrize("stored", [False, True])
async def test_a_replaced_conversation_keeps_its_folder_until_stored(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, stored: bool
) -> None:
    """A failed import leaves the conversation whole, its files included."""
    folder = tmp_dir(data_dir, owned("c1"))
    folder.mkdir(parents=True)

    async def import_conversation(*_args: object, **_kwargs: object) -> object:
        assert folder.exists()

        if not stored:
            raise ValueError

        return object()

    monkeypatch.setattr(conversation_files, "import_conversation", import_conversation)

    with nullcontext() if stored else pytest.raises(ValueError):
        await conversation_files.replace_conversation(owned("c1"), [])

    assert folder.exists() != stored
