"""Unit tests for canonical workspace-path resolution and its permission boundary."""

import pytest
from fastapi import HTTPException

from hivegent.server.common import resolve_workspace_path
from hivegent.store import Casebase
from hivegent.types import User

_USER = User(
    id="alice",
    read_groups=frozenset({"readers"}),
    write_groups=frozenset({"team"}),
)


def test_personal_path_resolves_to_user_store() -> None:
    store, local = resolve_workspace_path(_USER, "docs/report.md")
    assert store == Casebase.for_user("alice")
    assert local == "docs/report.md"


def test_at_prefix_without_slash_stays_personal() -> None:
    # The group rule requires a "/"; a bare "@name" is an ordinary filename.
    store, local = resolve_workspace_path(_USER, "@weird.md")
    assert store == Casebase.for_user("alice")
    assert local == "@weird.md"


def test_group_path_resolves_to_group_store() -> None:
    store, local = resolve_workspace_path(_USER, "@team/docs/report.md")
    assert store == Casebase.for_group("team")
    assert local == "docs/report.md"


def test_group_scope_root_has_empty_local() -> None:
    store, local = resolve_workspace_path(_USER, "@team/")
    assert store == Casebase.for_group("team")
    assert local == ""


def test_read_only_group_allows_read_but_denies_write() -> None:
    store, _ = resolve_workspace_path(_USER, "@readers/x.md")
    assert store == Casebase.for_group("readers")
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "@readers/x.md", write=True)
    assert exc.value.status_code == 403


def test_non_member_group_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "@secret/x.md")
    assert exc.value.status_code == 403
