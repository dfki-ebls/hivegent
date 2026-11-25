"""Unit tests for canonical workspace-path resolution and its permission boundary."""

import dataclasses
from pathlib import Path

import pytest
from fastapi import HTTPException

from hivegent.db import documents as db_documents
from hivegent.server.common import resolve_workspace_path
from hivegent.store import (
    AccessDenied,
    GlobalScope,
    Grant,
    GroupScope,
    HostGrant,
    User,
    UserScope,
    Workspace,
)
from hivegent.workspace import Root, locks

_USER = User(
    id="alice",
    read_groups=frozenset({"readers"}),
    write_groups=frozenset({"team"}),
)


def test_personal_path_resolves_to_user_store() -> None:
    store, local = resolve_workspace_path(_USER, "~/docs/report.md")
    assert store == HostGrant().workspace(UserScope("alice"))
    assert local == "docs/report.md"


def test_personal_scope_root_has_empty_local() -> None:
    store, local = resolve_workspace_path(_USER, "~")
    assert store == HostGrant().workspace(UserScope("alice"))
    assert local == ""


def test_bare_path_is_rejected() -> None:
    # Personal documents must carry the explicit "~" prefix.
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "report.md")
    assert exc.value.status_code == 400


def test_group_path_resolves_to_group_store() -> None:
    store, local = resolve_workspace_path(_USER, "@team/docs/report.md")
    assert store == HostGrant().workspace(GroupScope("team"))
    assert local == "docs/report.md"


def test_group_scope_root_without_slash_has_empty_local() -> None:
    store, local = resolve_workspace_path(_USER, "@team")
    assert store == HostGrant().workspace(GroupScope("team"))
    assert local == ""


def test_read_only_group_allows_read_but_denies_write() -> None:
    store, _ = resolve_workspace_path(_USER, "@readers/x.md")
    assert store == HostGrant().workspace(GroupScope("readers"))
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "@readers/x.md", write=True)
    assert exc.value.status_code == 403


def test_non_member_group_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "@secret/x.md")
    assert exc.value.status_code == 403


def test_workspaces_carry_the_token_grant_in_scope_order() -> None:
    assert [(s.store_key, s.writable) for s in _USER.workspaces()] == [
        ("user:alice", True),
        ("group:readers", False),
        ("group:team", True),
        ("global", False),
    ]
    assert [s.store_key for s in _USER.workspaces(writable=True)] == [
        "user:alice",
        "group:team",
    ]


def test_only_an_admin_writes_the_global_workspace() -> None:
    admin = User(id="root", roles=frozenset({"admin"}))

    assert resolve_workspace_path(_USER, "/srv/a.md")[0] == HostGrant().workspace(
        GlobalScope()
    )
    assert resolve_workspace_path(admin, "/srv/a.md", write=True)[0].writable

    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, "/srv/a.md", write=True)
    assert exc.value.status_code == 403

    # The write path itself refuses the read grant, not only the route.
    with pytest.raises(AccessDenied):
        locks.root_lock(Root.of(_USER.workspace("/srv")))


def test_a_grant_is_minted_only_by_a_user_or_the_host() -> None:
    store = _USER.workspace("@readers")

    with pytest.raises(TypeError):
        _ = Workspace(GlobalScope(), Grant.WRITE)  # pyright: ignore[reportCallIssue]  # ty: ignore[too-many-positional-arguments]

    with pytest.raises(TypeError):
        _ = dataclasses.replace(store, grant=Grant.WRITE)


def test_a_service_sees_the_global_workspace_alone() -> None:
    bot = User(
        id="bot",
        write_groups=frozenset({"team"}),
        roles=frozenset({"admin"}),
        kind="service",
    )

    assert bot.scopes() == (GlobalScope(),)
    assert bot.workspaces(writable=True) == ()

    with pytest.raises(AccessDenied):
        _ = bot.home


def test_locate_refuses_a_symlink_out_of_the_workspace(data_dir: Path) -> None:
    other = HostGrant().workspace(UserScope("bob")).workspace_dir()
    (other / "secret.md").write_text("bob's")
    workspace = _USER.home.workspace_dir()
    (workspace / "link.md").symlink_to(other / "secret.md")
    (workspace / "own.md").write_text("alice's")

    assert _USER.home.locate("link.md") is None
    assert _USER.home.locate("../user:bob/secret.md") is None
    assert _USER.home.locate("own.md") == workspace.resolve() / "own.md"


def test_a_read_grant_is_refused_by_every_write_path() -> None:
    readers = _USER.workspace("@readers")

    with pytest.raises(AccessDenied):
        locks.root_lock(Root.of(readers))

    with pytest.raises(AccessDenied):
        db_documents._writable_filter(readers)

    with pytest.raises(AccessDenied):
        db_documents._writable_key(readers)

    _ = locks.root_lock(Root.of(_USER.workspace("@team", writable=True)))
