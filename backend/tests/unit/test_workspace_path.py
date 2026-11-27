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


@pytest.mark.parametrize(
    ("path", "scope", "local"),
    [
        ("~/docs/report.md", UserScope("alice"), "docs/report.md"),
        ("~", UserScope("alice"), ""),
        ("@team/docs/report.md", GroupScope("team"), "docs/report.md"),
        ("@team", GroupScope("team"), ""),
        ("@readers/x.md", GroupScope("readers"), "x.md"),
    ],
)
def test_a_scope_prefix_resolves_to_its_workspace(
    path: str, scope: UserScope | GroupScope, local: str
) -> None:
    assert resolve_workspace_path(_USER, path) == (
        HostGrant().workspace(scope),
        local,
    )


@pytest.mark.parametrize(
    ("path", "write", "status"),
    [
        ("report.md", False, 400),
        ("@secret/x.md", False, 403),
        ("@readers/x.md", True, 403),
        ("/srv/a.md", True, 403),
    ],
)
def test_a_path_without_the_grant_is_refused(
    path: str, write: bool, status: int
) -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(_USER, path, write=write)

    assert exc.value.status_code == status


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

    for read_only in (readers, _USER.workspace("/srv")):
        with pytest.raises(AccessDenied):
            locks.root_lock(Root.of(read_only))

    with pytest.raises(AccessDenied):
        db_documents._writable_filter(readers)

    with pytest.raises(AccessDenied):
        db_documents._writable_key(readers)

    _ = locks.root_lock(Root.of(_USER.workspace("@team", writable=True)))
