"""Unit tests for turning a sandbox overlay into one changeset.

The overlay is driven through the mount's own methods, the way a program
drives it, and the staged operations are what the gateway would be handed.
What approving them shows is the planner's, tested with the gateway.
"""

from pathlib import Path, PurePosixPath

import pytest
from pydantic import TypeAdapter
from pydantic_monty import OSAccess

from hivegent import staging
from hivegent.changes import Changeset, CreateDir, Delete, Move, Write
from hivegent.config import settings
from hivegent.store import WorkspaceScope
from hivegent.tmp import TMP_SCOPE
from hivegent.tools.base import Direct, SearchPath, ToolRetry
from hivegent.tools.changeset import stage_changes
from hivegent.tools.workspace_os import WORKSPACE_MOUNT, WorkspaceOS
from tests.helpers import LIMITS


@pytest.fixture()
def mount(tmp_path: Path) -> WorkspaceOS:
    workspace, tmp = tmp_path / "workspace", tmp_path / "tmp"
    (workspace / "report.assets").mkdir(parents=True)
    (workspace / "report.pdf").write_bytes(b"%PDF")
    (workspace / "report.md").write_text("![](report.assets/fig.png)\n")
    (workspace / "notes.md").write_text("alpha\n")
    (workspace / "old").mkdir()
    (workspace / "old" / "a.md").write_text("a\n")
    (tmp / "keep").mkdir(parents=True)
    (tmp / "draft.md").write_text("draft\n")
    root = SearchPath(path=workspace, scope=WorkspaceScope())
    roots = (root, SearchPath(path=tmp, scope=TMP_SCOPE, policy=Direct()))

    return WorkspaceOS(
        paths=roots, inner=OSAccess([], environ={}), limits=LIMITS, writable=roots
    )


def _virtual(local: str) -> PurePosixPath:
    return WORKSPACE_MOUNT / f"~/{local}"


def _staged(mount: WorkspaceOS) -> Changeset[str]:
    staged = stage_changes(mount)
    assert staged is not None
    adapter = TypeAdapter(Changeset[str])
    assert adapter.validate_json(adapter.dump_json(staged)) == staged

    return staged


def test_an_unchanged_overlay_stages_nothing(mount: WorkspaceOS) -> None:
    _ = mount.path_write_text(_virtual("notes.md"), "alpha\n")

    assert stage_changes(mount) is None


def test_an_entry_renamed_whole_is_one_move_and_a_write_follows_it(
    mount: WorkspaceOS,
) -> None:
    mount.path_rename(_virtual("report.pdf"), _virtual("archive/2024.pdf"))
    mount.path_rename(_virtual("report.md"), _virtual("archive/2024.md"))
    _ = mount.path_append_text(_virtual("archive/2024.md"), "seen\n")

    staged = _staged(mount)

    move, write = staged.operations
    assert move == Move("~/report.md", "~/archive/2024.md", mount.bases["~/report.md"])
    assert isinstance(write, Write)
    assert (write.target, write.mode) == ("~/archive/2024.md", "replace")
    # The move repoints the description's payload, so the write keeps it pointed.
    assert write.content == "![](2024.assets/fig.png)\nseen\n"


def test_renaming_or_removing_the_original_takes_the_whole_entry(
    mount: WorkspaceOS,
) -> None:
    mount.path_rename(_virtual("report.pdf"), _virtual("renamed.pdf"))
    staged = _staged(mount)

    assert staged.operations == (
        Move("~/report.pdf", "~/renamed.pdf", mount.bases["~/report.pdf"]),
    )

    mount.path_unlink(_virtual("renamed.pdf"))
    staged = _staged(mount)

    assert staged.operations == (Delete("~/report.pdf", mount.bases["~/report.pdf"]),)


def test_removing_only_the_description_is_refused(mount: WorkspaceOS) -> None:
    mount.path_unlink(_virtual("report.md"))

    with pytest.raises(ToolRetry, match="searchable text of '~/report.pdf'"):
        _ = stage_changes(mount)


def test_a_rename_onto_a_file_replaces_it(mount: WorkspaceOS) -> None:
    mount.path_rename(_virtual("old/a.md"), _virtual("notes.md"))
    staged = _staged(mount)

    assert staged.operations == (
        Move("~/old/a.md", "~/notes.md", mount.bases["~/old/a.md"]),
        Delete("~/notes.md", mount.bases["~/notes.md"]),
    )


def test_a_case_only_rename_is_a_move(mount: WorkspaceOS) -> None:
    mount.path_rename(_virtual("notes.md"), _virtual("Notes.md"))

    assert mount.path_read_text(_virtual("Notes.md")) == "alpha\n"
    assert _staged(mount).operations == (
        Move("~/notes.md", "~/Notes.md", mount.bases["~/notes.md"]),
    )


def test_only_an_otherwise_empty_new_directory_is_created(mount: WorkspaceOS) -> None:
    mount.path_mkdir(_virtual("empty/deep"), parents=True, exist_ok=False)
    mount.path_mkdir(_virtual("full"), parents=False, exist_ok=False)
    _ = mount.path_write_text(_virtual("full/a.md"), "a")

    assert _staged(mount).operations == (
        Write("~/full/a.md", "a", "create"),
        CreateDir("~/empty/deep"),
    )


def test_directories_move_and_go_whole(mount: WorkspaceOS) -> None:
    mount.path_rename(_virtual("old"), _virtual("new"))
    _ = mount.path_write_text(_virtual("new/b.md"), "b\n")
    mount.path_unlink(_virtual("notes.md"))

    staged = _staged(mount)

    assert staged.operations == (
        Move("~/old", "~/new"),
        Delete("~/notes.md", mount.bases["~/notes.md"]),
        Write("~/new/b.md", "b\n", "create"),
    )


def test_a_change_inside_a_moved_directory_names_the_file_as_it_is_now(
    mount: WorkspaceOS,
) -> None:
    mount.path_rename(_virtual("old"), _virtual("new"))
    mount.path_unlink(_virtual("new/a.md"))

    assert _staged(mount).operations == (
        Move("~/old", "~/new"),
        Delete("~/old/a.md", mount.bases["~/old/a.md"]),
    )


def test_emptying_a_directory_moves_its_documents_then_removes_it(
    mount: WorkspaceOS,
) -> None:
    mount.path_rename(_virtual("old/a.md"), _virtual("a.md"))
    mount.path_rmdir(_virtual("old"))

    assert _staged(mount).operations == (
        Move("~/old/a.md", "~/a.md", mount.bases["~/old/a.md"]),
        Delete("~/old"),
    )


def test_tmp_holds_plain_files_and_a_rename_across_roots_copies(
    mount: WorkspaceOS,
) -> None:
    """`/tmp` commits apart from the workspace, so a rename between them is a copy."""
    tmp = PurePosixPath("/tmp")
    mount.path_rename(tmp / "draft.md", _virtual("final.md"))
    mount.path_mkdir(tmp / "keep/deep", parents=False, exist_ok=False)
    _ = mount.path_write_text(tmp / "state.json", "{}")
    mount.path_rename(tmp / "state.json", tmp / "keep/deep/state.json")
    mount.path_rename(_virtual("notes.md"), tmp / "notes.md")

    assert set(_staged(mount).operations) == {
        Write("~/final.md", "draft\n", "create"),
        Delete("~/notes.md", mount.bases["~/notes.md"]),
        Delete("/tmp/draft.md"),
        CreateDir("/tmp/keep/deep"),
        Write("/tmp/keep/deep/state.json", "{}"),
        Write("/tmp/notes.md", "alpha\n"),
    }

    with pytest.raises(IsADirectoryError):
        mount.path_rename(tmp / "keep", _virtual("elsewhere"))


def test_a_ragged_table_is_refused(mount: WorkspaceOS) -> None:
    _ = mount.path_write_text(_virtual("table.csv"), "a,b\n1,2,3\n")

    with pytest.raises(ToolRetry, match="line 2 has 3 fields"):
        _ = stage_changes(mount)


async def test_a_staged_changeset_answers_only_its_owner_and_its_own_id(
    mount: WorkspaceOS, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    mount.path_unlink(_virtual("notes.md"))
    changes = _staged(mount)

    changeset_id = await staging.stage("alice", changes)

    assert await staging.load_staged("alice", changeset_id) == changes
    assert await staging.load_staged("bob", changeset_id) is None
    assert await staging.load_staged("alice", f"../changesets/{changeset_id}") is None

    await staging.discard_staged("bob", changeset_id)
    assert await staging.load_staged("alice", changeset_id) == changes

    await staging.discard_staged("alice", changeset_id)
    assert await staging.load_staged("alice", changeset_id) is None
