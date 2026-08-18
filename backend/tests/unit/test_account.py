"""Tests for account cleanup routes."""

from unittest.mock import Mock

import pytest

from hivegent.auth import User
from hivegent.server.routes import account
from hivegent.store import Casebase


async def test_delete_all_user_data_notifies_other_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful personal workspace reset announces its changed scope."""

    async def noop(*_args: object) -> None:
        pass

    notify = Mock()
    monkeypatch.setattr(account.workspace, "delete_all", noop)
    monkeypatch.setattr(account, "delete_user", noop)
    monkeypatch.setattr(account, "notify_workspace_change", notify)

    await account.delete_all_user_data(User(id="owner"), "acting-tab")

    notify.assert_called_once_with("owner", Casebase.for_user("owner"), "acting-tab")
