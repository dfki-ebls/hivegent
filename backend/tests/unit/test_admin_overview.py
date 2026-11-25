"""The admin overview lists users and groups with their counts."""

import pytest

from hivegent.server.routes import admin
from hivegent.types import AdminGroupInfo, AdminUserInfo


async def test_overview_lists_users_and_groups_with_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def users() -> list[tuple[str, int, int]]:
        return [("alice", 3, 2)]

    async def groups() -> list[tuple[str, int]]:
        return [("research", 5)]

    monkeypatch.setattr(admin, "list_users_with_counts", users)
    monkeypatch.setattr(admin, "list_groups_with_counts", groups)

    overview = await admin.get_overview()

    assert overview.users == [
        AdminUserInfo(id="alice", document_count=3, conversation_count=2)
    ]
    assert overview.groups == [AdminGroupInfo(id="research", document_count=5)]
