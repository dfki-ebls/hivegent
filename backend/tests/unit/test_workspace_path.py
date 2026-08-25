"""Unit tests for canonical workspace-path resolution and its permission boundary."""

import pytest
from fastapi import HTTPException

from hivegent.server.common import resolve_workspace_path
from hivegent.store import Casebase
from hivegent.types import User
from hivegent.workspace.paths import _check_not_reserved_path

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


@pytest.mark.parametrize(
    "path",
    [
        ".scratch/state.json",
        "notes/.scratch/state.json",
        "notes/report.assets/fig1.png",
    ],
)
def test_reserved_directories_are_closed_to_the_generic_api(path: str) -> None:
    """Upload, move, and create-directory refuse the layers the workspace owns.

    Both reserved names would otherwise accept content the user can never see
    again: an `.assets` payload is disowned by its entry, and a `.scratch` file
    is deleted at the next boot.  Scratch is reachable only through the write
    tools, which is the one path it is meant to be written by.
    """
    with pytest.raises(HTTPException) as exc_info:
        _check_not_reserved_path(path)

    assert exc_info.value.status_code == 400


def test_ordinary_paths_pass_the_reserved_check() -> None:
    _check_not_reserved_path("notes/report.md")
    _check_not_reserved_path("notes/scratch/report.md")
