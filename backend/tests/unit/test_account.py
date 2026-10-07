"""Tests for the account and conversation cleanup routes."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from hivegent.auth import User
from hivegent.server.routes import account, conversations
from hivegent.store import Casebase
from hivegent.tmp import tmp_dir


async def test_delete_all_user_data_notifies_other_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful personal workspace reset announces its changed scope."""

    async def noop(*_args: object) -> None:
        pass

    async def no_conversations(*_args: object) -> set[str]:
        return set()

    notify = Mock()
    monkeypatch.setattr(account.workspace, "delete_all", noop)
    monkeypatch.setattr(account, "conversation_ids", no_conversations)
    monkeypatch.setattr(account, "delete_user", noop)
    monkeypatch.setattr(account, "notify_workspace_change", notify)

    await account.delete_all_user_data(User(id="owner"), "acting-tab")

    notify.assert_called_once_with("owner", Casebase.for_user("owner"), "acting-tab")


async def test_tmp_folders_go_by_the_conversation_ids_the_database_names(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One conversation, every one of them, and clearing temporary files, each by id."""
    folders = {cid: tmp_dir(data_dir, cid) for cid in ("c1", "c2", "c3")}

    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}")

    async def removed(*_args: object) -> bool:
        return True

    async def deleted_all(_user_id: str) -> list[str]:
        return ["c2"]

    async def owned(_user_id: str) -> set[str]:
        return {"c3", "never-ran"}

    monkeypatch.setattr(conversations, "remove_conversation", removed)
    monkeypatch.setattr(conversations, "delete_all_conversations", deleted_all)
    monkeypatch.setattr(account, "conversation_ids", owned)
    user = User(id="owner")

    await conversations.delete_conversation("c1", user)
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c2", "c3"]

    await conversations.delete_all_conversations_route(user)
    assert [cid for cid, folder in folders.items() if folder.exists()] == ["c3"]

    assert (await account.delete_tmp(user)).files_removed == 1
    assert not any(folder.exists() for folder in folders.values())
