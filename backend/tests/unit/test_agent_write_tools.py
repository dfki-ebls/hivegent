"""Unit tests for the workspaces the agent's mutating tools can reach.

The write tools search the personal workspace plus the groups the user may
write to, and route each accepted path back to the workspace that claimed it,
so a path copied out of a listing lands where it says it does.
"""

from pathlib import Path

import pytest

from hivegent import workspace
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.write import _write_document
from hivegent.store import Casebase
from hivegent.tools.base import ToolRetry


@pytest.fixture()
def deps(data_dir: Path) -> UserDeps:
    """Deps with one writable and one read-only group."""
    _ = data_dir
    return UserDeps(
        user_id="u",
        store=Casebase.for_user("u"),
        group_stores=(Casebase.for_group("team"), Casebase.for_group("archive")),
        write_group_stores=(Casebase.for_group("team"),),
    )


@pytest.fixture()
def routed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record the ``(store_key, local_path)`` each write is routed to."""
    calls: list[tuple[str, str]] = []

    async def _write(store: Casebase, safe: str, *_args: object, **_kw: object) -> str:
        calls.append((store.store_key, safe))
        return "written"

    monkeypatch.setattr(workspace, "write_document_text", _write)
    return calls


async def test_writes_to_the_addressed_workspace(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = _write_document(deps)

    assert (await tool("@team/notes/a.md", "hi")).data == "written"
    assert (await tool("~/notes/a.md", "hi")).data == "written"
    assert routed == [("group:team", "notes/a.md"), ("user:u", "notes/a.md")]


async def test_unprefixed_path_is_refused_with_the_roots_named(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    """Nothing is implied by context, so the refusal says what to write instead."""
    tool = _write_document(deps)

    with pytest.raises(ToolRetry, match=r"addresses ~, @team; give the full path"):
        await tool("notes/a.md", "hi")

    assert routed == []


async def test_refuses_a_group_the_user_may_only_read(
    deps: UserDeps, routed: list[tuple[str, str]]
) -> None:
    tool = _write_document(deps)

    with pytest.raises(ToolRetry, match="not accessible"):
        await tool("@archive/notes/a.md", "hi")

    assert routed == []
