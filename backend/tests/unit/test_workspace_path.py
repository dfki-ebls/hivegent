"""Unit tests for canonical workspace-path resolution and its permission boundary."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from hivegent.db import documents as db_documents
from hivegent.server.common import resolve_workspace_path
from hivegent.store import AccessDenied, Casebase, User
from hivegent.workspace import locks

_USER = User(
    id="alice",
    read_groups=frozenset({"readers"}),
    write_groups=frozenset({"team"}),
)


def test_personal_path_resolves_to_user_store() -> None:
    store, local = resolve_workspace_path(_USER, "~/docs/report.md")
    assert store == Casebase.for_user("alice")
    assert local == "docs/report.md"


def test_personal_scope_root_has_empty_local() -> None:
    store, local = resolve_workspace_path(_USER, "~")
    assert store == Casebase.for_user("alice")
    assert local == ""


def test_bare_path_is_rejected() -> None:
    # Personal documents must carry the explicit "~" prefix.
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "report.md")
    assert exc.value.status_code == 400


def test_group_path_resolves_to_group_store() -> None:
    store, local = resolve_workspace_path(_USER, "@team/docs/report.md")
    assert store == Casebase.for_group("team")
    assert local == "docs/report.md"


def test_group_scope_root_without_slash_has_empty_local() -> None:
    store, local = resolve_workspace_path(_USER, "@team")
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


def test_group_stores_carry_the_token_grant() -> None:
    assert [(s.id, s.writable) for s in _USER.group_stores()] == [
        ("readers", False),
        ("team", True),
    ]
    assert [s.id for s in _USER.group_stores(writable=True)] == ["team"]


def test_locate_refuses_a_symlink_out_of_the_workspace(data_dir: Path) -> None:
    other = Casebase.for_user("bob").workspace_dir(data_dir)
    (other / "secret.md").write_text("bob's")
    workspace = _USER.store.workspace_dir(data_dir)
    (workspace / "link.md").symlink_to(other / "secret.md")
    (workspace / "own.md").write_text("alice's")

    assert _USER.store.locate("link.md") is None
    assert _USER.store.locate("../user:bob/secret.md") is None
    assert _USER.store.locate("own.md") == workspace.resolve() / "own.md"


def test_a_read_grant_is_refused_by_every_write_path() -> None:
    readers = _USER.group_store("readers")

    with pytest.raises(AccessDenied):
        locks._lock_of(readers)

    with pytest.raises(AccessDenied):
        db_documents._writable_filter(readers)

    with pytest.raises(AccessDenied):
        db_documents._owner_kwargs(readers)

    _ = locks._lock_of(_USER.group_store("team", writable=True))
