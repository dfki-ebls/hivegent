"""Unit tests for the copy-on-write workspace mount the sandbox runs against.

Mostly exercised without a sandbox: the mount is an ordinary object, so the
semantics that matter, what a program may see, read, and change, are asserted
against its methods rather than through an interpreter that would only relay
them.
"""

from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest
from pydantic_monty import OSAccess

from hivegent import entries
from hivegent.changes import Changeset, Write
from hivegent.store import WorkspaceScope
from hivegent.tmp import TMP_SCOPE
from hivegent.tools.base import Direct, SearchPath
from hivegent.tools.changeset import stage_changes
from hivegent.tools.workspace_os import (
    WORKSPACE_MOUNT,
    ChangesetLimits,
    Deleted,
    Ref,
    WorkspaceOS,
)
from hivegent.types import DocumentFilter


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "reports").mkdir(parents=True)
    (workspace / "notes.md").write_text("alpha\n")
    (workspace / "reports" / "q1.md").write_text("beta\n")
    (workspace / "hidden.md").write_text("secret\n")
    (workspace / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    (tmp_path / "tmp").mkdir()

    return workspace


def _mount(workspace: Path, *, writable: bool = False) -> WorkspaceOS:
    """The mount a run gets, `/tmp` writable in every mode as it is there."""
    scoped = SearchPath(
        path=workspace,
        scope=WorkspaceScope(),
        filter_func=DocumentFilter(excluded=frozenset({"hidden.md"})),
    )
    tmp = SearchPath(path=workspace.with_name("tmp"), scope=TMP_SCOPE, policy=Direct())

    return WorkspaceOS(
        paths=(scoped, tmp),
        inner=OSAccess([], environ={}),
        limits=ChangesetLimits(max_operations=200, max_deletes=100, max_chars=10_000),
        writable=(scoped, tmp) if writable else (tmp,),
    )


def _virtual(local: str) -> PurePosixPath:
    """A document as a program opens it, resolved against the working directory."""
    return WORKSPACE_MOUNT / f"~/{local}"


def test_mount_root_lists_the_workspaces(workspace: Path) -> None:
    mount = _mount(workspace)

    assert mount.path_is_dir(WORKSPACE_MOUNT)
    assert mount.path_iterdir(WORKSPACE_MOUNT) == [PurePosixPath("~")]
    assert mount.path_iterdir(WORKSPACE_MOUNT / "~") == [
        PurePosixPath("~/notes.md"),
        PurePosixPath("~/picture.png"),
        PurePosixPath("~/reports"),
    ]


def test_filtered_document_is_absent_rather_than_refused(workspace: Path) -> None:
    # A document the user hid must be indistinguishable from one that is not
    # there, or the refusal itself reports what the filter hides.
    mount = _mount(workspace)

    assert not mount.path_exists(_virtual("hidden.md"))
    with pytest.raises(FileNotFoundError):
        _ = mount.path_read_text(_virtual("hidden.md"))


def test_traversal_and_binary_reads_are_refused(workspace: Path) -> None:
    mount = _mount(workspace)

    with pytest.raises(FileNotFoundError):
        _ = mount.path_read_text(_virtual("../../etc/passwd"))

    with pytest.raises(ValueError, match="not text-like"):
        _ = mount.path_read_text(_virtual("picture.png"))

    with pytest.raises(ValueError, match="no bytes"):
        _ = mount.path_read_bytes(_virtual("notes.md"))


def test_dispatch_mounts_tmp_and_refuses_every_other_absolute_path(
    workspace: Path,
) -> None:
    """Through ``dispatch``, the way Monty reaches the filesystem."""
    mount = _mount(workspace)

    handle = mount.dispatch("open", (_virtual("notes.md"), "r"))
    assert mount.dispatch("Path.read_text", (handle,)) == "alpha\n"

    # `/tmp` is the overlay too, so even read mode writes it, and reads it back.
    private = mount.dispatch("open", (PurePosixPath("/tmp/work.txt"), "w"))
    _ = mount.dispatch("Path.write_text", (private, "intermediate"))
    _ = mount.dispatch("Path.append_text", (PurePosixPath("/tmp/work.txt"), "!"))
    assert mount.dispatch("Path.read_text", (PurePosixPath("/tmp/work.txt"),)) == (
        "intermediate!"
    )
    assert mount.dispatch("Path.iterdir", (PurePosixPath("/tmp"),)) == [
        PurePosixPath("/tmp/work.txt")
    ]

    with pytest.raises(FileNotFoundError):
        _ = mount.dispatch("open", (_virtual("missing.md"), "r"))

    with pytest.raises(PermissionError, match="outside this run's filesystem"):
        _ = mount.dispatch("Path.write_text", (PurePosixPath("/var/x.txt"), "x"))


def test_exact_budget_is_enforced_on_the_decoded_text(workspace: Path) -> None:
    # The size pre-check is a conservative bound (four bytes per character), so
    # ASCII text passes it well over the character budget and only the check on
    # the decoded string is exact.
    mount = _mount(workspace)
    mount.max_document_chars = 4

    with pytest.raises(MemoryError, match="~/notes.md"):
        _ = mount.path_read_text(_virtual("notes.md"))


def test_reading_many_documents_is_not_a_running_total(workspace: Path) -> None:
    # The cap is per document because a decoded document is what the host
    # holds, one at a time.  A run that reads the whole workspace is what the
    # mount is for, so nothing may accumulate against it.
    mount = _mount(workspace)
    mount.max_document_chars = len("alpha\n")

    for _ in range(50):
        assert mount.path_read_text(_virtual("notes.md")) == "alpha\n"


def test_writes_are_staged_and_read_back_without_touching_the_disk(
    workspace: Path,
) -> None:
    mount = _mount(workspace, writable=True)

    _ = mount.path_write_text(_virtual("notes.md"), "rewritten\n")
    _ = mount.path_append_text(_virtual("notes.md"), "more\n")
    _ = mount.path_write_text(_virtual("new/deep.md"), "fresh")

    assert mount.path_read_text(_virtual("notes.md")) == "rewritten\nmore\n"
    assert mount.path_read_text(_virtual("new/deep.md")) == "fresh"
    assert mount.path_is_dir(_virtual("new"))
    assert (workspace / "notes.md").read_text() == "alpha\n"
    assert not (workspace / "new").exists()


def test_a_rename_carries_the_file_rather_than_its_bytes(workspace: Path) -> None:
    """A binary moves as freely as text, since nothing ever reads it."""
    mount = _mount(workspace, writable=True)

    mount.path_rename(_virtual("picture.png"), _virtual("images/photo.png"))
    mount.path_rename(_virtual("reports"), _virtual("archive"))

    assert mount.nodes["~/images/photo.png"] == Ref("~/picture.png")
    assert mount.nodes["~/archive"] == Ref("~/reports")
    assert not mount.path_exists(_virtual("picture.png"))
    assert mount.path_read_text(_virtual("archive/q1.md")) == "beta\n"
    assert mount.path_iterdir(WORKSPACE_MOUNT / "~") == [
        PurePosixPath("~/archive"),
        PurePosixPath("~/images"),
        PurePosixPath("~/notes.md"),
    ]
    assert (workspace / "picture.png").exists()

    with pytest.raises(PermissionError, match="keeps its extension"):
        mount.path_rename(_virtual("notes.md"), _virtual("notes.txt"))

    # Onto a file, a rename replaces it, and a directory takes part in no such swap.
    mount.path_rename(_virtual("notes.md"), _virtual("archive/q1.md"))
    assert mount.path_read_text(_virtual("archive/q1.md")) == "alpha\n"

    with pytest.raises(FileExistsError):
        mount.path_rename(_virtual("archive"), _virtual("images"))


def test_a_case_insensitive_workspace_keeps_one_key_per_file(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entries, "folds_case", lambda _directory: True)
    mount = _mount(workspace, writable=True)

    _ = mount.path_write_text(_virtual("NOTES.md"), "rewritten\n")
    # A file the program created keeps the spelling it was created under.
    _ = mount.path_write_text(_virtual("Drafts/New.md"), "new\n")
    _ = mount.path_append_text(_virtual("drafts/new.md"), "more\n")

    assert mount.path_read_text(_virtual("Notes.md")) == "rewritten\n"
    assert mount.path_read_text(_virtual("DRAFTS/NEW.md")) == "new\nmore\n"
    assert set(mount.nodes) == {"~/notes.md", "~/Drafts", "~/Drafts/New.md"}


def test_removals_merge_into_listings(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)

    with pytest.raises(OSError, match="not empty"):
        mount.path_rmdir(_virtual("reports"))

    mount.path_unlink(_virtual("reports/q1.md"))
    mount.path_rmdir(_virtual("reports"))
    mount.path_mkdir(_virtual("drafts"), parents=False, exist_ok=False)

    assert mount.path_iterdir(WORKSPACE_MOUNT / "~") == [
        PurePosixPath("~/drafts"),
        PurePosixPath("~/notes.md"),
        PurePosixPath("~/picture.png"),
    ]
    assert mount.nodes["~/reports"] == Deleted()
    assert (workspace / "reports" / "q1.md").exists()


def test_what_a_program_may_not_change(workspace: Path) -> None:
    (workspace / "report.assets").mkdir()
    (workspace / "report.assets" / "fig.png").write_bytes(b"x")
    mount = _mount(workspace, writable=True)

    # An entry's payload is the workspace's to manage, so no program sees it.
    assert not mount.path_exists(_virtual("report.assets"))
    refusals: list[tuple[Callable[[], object], type[Exception]]] = [
        (lambda: mount.path_write_text(_virtual("report.assets/a.md"), "x"), PermissionError),
        (lambda: mount.path_write_text(_virtual("sheet.xlsx"), "x"), ValueError),
        (lambda: mount.path_write_text(_virtual("picture.png"), "x"), ValueError),
        (lambda: mount.path_write_bytes(_virtual("notes.md"), b"x"), ValueError),
        # A rename into `/tmp` copies the text, which a binary has none of.
        (
            lambda: mount.path_rename(_virtual("picture.png"), PurePosixPath("/tmp/p.png")),
            ValueError,
        ),
    ]

    for act, error in refusals:
        with pytest.raises(error):
            _ = act()

    assert mount.nodes == {}


def test_read_mode_changes_nothing(workspace: Path) -> None:
    mount = _mount(workspace)

    for act in (
        lambda: mount.path_write_text(_virtual("state.json"), "{}"),
        lambda: mount.path_unlink(_virtual("notes.md")),
        lambda: mount.path_rename(_virtual("notes.md"), _virtual("moved.md")),
    ):
        with pytest.raises(PermissionError, match="may change /tmp"):
            _ = act()

    assert mount.path_read_text(_virtual("notes.md")) == "alpha\n"


def test_limits_hold_across_the_run(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)
    mount.limits = ChangesetLimits(max_operations=2, max_deletes=1, max_chars=10)

    _ = mount.path_write_text(_virtual("a.txt"), "x" * 8)

    with pytest.raises(MemoryError, match="characters"):
        _ = mount.path_write_text(_virtual("a.txt"), "y" * 8)

    mount.path_unlink(_virtual("notes.md"))

    with pytest.raises(PermissionError, match="paths"):
        mount.path_unlink(_virtual("reports/q1.md"))


def test_a_new_directory_charges_one_operation(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)
    mount.limits = ChangesetLimits(max_operations=1, max_deletes=1, max_chars=100)
    mount.path_mkdir(_virtual("drafts"), parents=False, exist_ok=False)
    mount.path_mkdir(_virtual("drafts"), parents=False, exist_ok=True)

    with pytest.raises(PermissionError, match="paths"):
        mount.path_mkdir(_virtual("other"), parents=False, exist_ok=False)


def test_a_write_into_new_directories_charges_only_the_write(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)
    mount.limits = ChangesetLimits(max_operations=1, max_deletes=1, max_chars=100)
    _ = mount.path_write_text(_virtual("drafts/deep/new.txt"), "new")

    assert stage_changes(mount) == Changeset((Write("~/drafts/deep/new.txt", "new", "create"),))


def test_tmp_directories_are_not_charged(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)
    mount.limits = ChangesetLimits(max_operations=0, max_deletes=0, max_chars=100)
    mount.path_mkdir(PurePosixPath("/tmp/a/b"), parents=True, exist_ok=False)

    assert mount.path_is_dir(PurePosixPath("/tmp/a/b"))


def test_a_path_leading_with_no_workspace_names_the_roots(workspace: Path) -> None:
    # Dropping the scope between the mount and the document is the one miss the
    # failure cannot correct on its own, so the refusal spells the roots out.
    mount = _mount(workspace)

    with pytest.raises(FileNotFoundError, match="leads with ~, /tmp"):
        _ = mount.path_read_text(WORKSPACE_MOUNT / "notes.md")

    # A path that does lead with a root missed for another reason, and listing
    # roots would only muddle that.
    with pytest.raises(FileNotFoundError, match=r"^(?!.*leads with)"):
        _ = mount.path_read_text(_virtual("missing.md"))


def test_a_directory_holding_what_the_program_cannot_see_is_not_removed(
    workspace: Path,
) -> None:
    (workspace / "pkg" / "node_modules").mkdir(parents=True)
    (workspace / "pkg" / "node_modules" / "lib.js").write_text("x")
    (workspace / "pkg" / "out.md").write_text("x")
    mount = _mount(workspace, writable=True)

    mount.path_unlink(_virtual("pkg/out.md"))
    assert mount.path_iterdir(_virtual("pkg")) == []

    with pytest.raises(OSError, match="not empty"):
        mount.path_rmdir(_virtual("pkg"))
