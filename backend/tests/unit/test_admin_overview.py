"""The admin overview lists users and groups with their counts."""

from hivegent.server.routes.admin import overview
from hivegent.types import AdminGroupInfo, AdminUserInfo


def test_overview_lists_users_and_groups_with_counts() -> None:
    result = overview([("alice", 3, 2)], [("research", 5)])

    assert result.users == [
        AdminUserInfo(id="alice", document_count=3, conversation_count=2)
    ]
    assert result.groups == [AdminGroupInfo(id="research", document_count=5)]
