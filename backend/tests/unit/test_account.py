"""Tests for the account and conversation cleanup routes."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from hivegent import conversation_files
from hivegent.changes import WorkspaceChanged
from hivegent.db.conversations import OwnedConversation
from hivegent.server import common
from hivegent.server.routes import account, conversations
from hivegent.store import User
from hivegent.tmp import tmp_dir
from tests.helpers import owned


async def test_delete_all_user_data_announces_to_other_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful personal workspace reset announces its changed scope."""

    async def noop(*_args: object) -> None:
        pass

    announce = Mock()
    monkeypatch.setattr(common.workspace, "delete_all", noop)
    monkeypatch.setattr(common, "delete_conversations", noop)
    monkeypatch.setattr(common, "delete_user", noop)
    monkeypatch.setattr(common, "announce_workspace_changed", announce)

    await account.delete_all_user_data(User(id="owner"), "acting-tab")

    announce.assert_called_once_with(
        "owner", WorkspaceChanged(("~",)), exclude_client="acting-tab"
    )


async def test_tmp_folders_go_by_the_conversation_ids_the_database_names(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One conversation, every one of them, and clearing temporary files, each by id."""
    user = User(id="owner")
    folders = {cid: tmp_dir(data_dir, owned(cid, user)) for cid in ("c1", "c2", "c3")}

    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")

    async def removed(*_args: object) -> bool:
        return True

    async def deleted_all(owner: User) -> list[OwnedConversation]:
        return [owned("c2", owner)]

    async def remaining(owner: User) -> list[OwnedConversation]:
        return [owned("c3", owner), owned("never-ran", owner)]

    monkeypatch.setattr(conversation_files, "remove_conversation", removed)
    monkeypatch.setattr(conversation_files, "delete_all_conversations", deleted_all)
    monkeypatch.setattr(account, "owned_conversations", remaining)

    await conversations.delete_conversation(owned("c1", user))
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c2", "c3"]

    await conversations.delete_all_conversations_route(user)
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c3"]

    assert (await account.delete_tmp(user)).files_removed == 1
    assert not any(folder.exists() for folder in folders.values())
