"""Tests for wiping one user or group on the host with ``hivegent reset``."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hivegent import cli, workspace
from hivegent.store import GroupScope, HostGrant, Workspace


@pytest.fixture()
def wiped(monkeypatch: pytest.MonkeyPatch) -> list[Workspace]:
    """The workspaces wiped from here on, in a database that holds no group."""
    wiped: list[Workspace] = []

    async def delete_all(store: Workspace) -> None:
        wiped.append(store)

    async def no_groups() -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr(workspace, "delete_all", delete_all)
    monkeypatch.setattr("hivegent.db.groups.list_group_ids", no_groups)

    return wiped


@pytest.mark.parametrize("args", [["user"], ["group"], ["workspace", "alice"]])
def test_an_owner_is_named_for_a_user_or_group_wipe_alone(args: list[str]) -> None:
    result = CliRunner().invoke(cli.app, ["reset", *args, "--yes"])

    assert result.exit_code == 1


async def test_a_group_wipe_deletes_its_workspace_and_skills_alone(
    data_dir: Path, wiped: list[Workspace]
) -> None:
    skills = data_dir / "skills"
    (skills / "group:team" / "count").mkdir(parents=True)
    (skills / "group:other").mkdir()

    await cli._reset_owner(cli.ResetTarget.GROUP, "team")

    assert wiped == [HostGrant().workspace(GroupScope("team"))]
    assert wiped[0].writable
    assert [path.name for path in skills.iterdir()] == ["group:other"]


@pytest.mark.usefixtures("wiped")
async def test_an_unknown_group_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown group"):
        await cli._reset_owner(cli.ResetTarget.GROUP, "team")
