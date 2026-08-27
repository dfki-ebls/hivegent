"""Unit tests for the read-only workspace mount the sandbox runs against.

Mostly exercised without a sandbox: the mount is an ordinary object, so the
semantics that matter, what a program may see, read, and change, are asserted
against its methods rather than through an interpreter that would only relay
them.
"""

from pathlib import Path, PurePosixPath

import pytest
from pydantic_monty import OSAccess

from hivegent.store import WorkspaceScope
from hivegent.tools.base import SearchPath
from hivegent.tools.workspace_os import WORKSPACE_MOUNT, WorkspaceOS


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("alpha\n")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "q1.md").write_text("beta\n")
    (tmp_path / "hidden.md").write_text("secret\n")
    (tmp_path / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    return tmp_path


def _mount(workspace: Path, *, writable: bool = False) -> WorkspaceOS:
    scoped = SearchPath(
        path=workspace,
        scope=WorkspaceScope(),
        filter_func=lambda local: local != "hidden.md",
    )
    return WorkspaceOS(
        paths=(scoped,),
        inner=OSAccess([], environ={}),
        writable=(scoped,) if writable else (),
    )


def _virtual(local: str) -> PurePosixPath:
    return WORKSPACE_MOUNT / f"~/{local}"


def test_mount_root_lists_the_workspaces(workspace: Path) -> None:
    mount = _mount(workspace)

    assert mount.path_is_dir(WORKSPACE_MOUNT)
    assert mount.path_iterdir(WORKSPACE_MOUNT) == [WORKSPACE_MOUNT / "~"]
    assert mount.path_iterdir(WORKSPACE_MOUNT / "~") == [
        _virtual("notes.md"),
        _virtual("picture.png"),
        _virtual("reports"),
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


def test_dispatch_splits_the_mount_from_the_run_s_own_files(workspace: Path) -> None:
    """Through ``dispatch``, the way Monty reaches the filesystem.

    Routing is decided once there rather than in each method, so a method the
    mount does not implement never strands the run's own files, which is how
    ``open`` came to refuse ``/tmp``.
    """
    mount = _mount(workspace)
    mount.inner.path_mkdir(PurePosixPath("/tmp"), parents=True, exist_ok=True)

    handle = mount.dispatch("open", (_virtual("notes.md"), "r"))
    assert mount.dispatch("Path.read_text", (handle,)) == "alpha\n"

    private = mount.dispatch("open", (PurePosixPath("/tmp/work.txt"), "w"))
    _ = mount.dispatch("Path.write_text", (private, "intermediate"))
    assert mount.dispatch("Path.read_text", (private,)) == "intermediate"
    _ = mount.dispatch("Path.append_text", (PurePosixPath("/tmp/work.txt"), "!"))
    assert mount.dispatch("Path.read_text", (PurePosixPath("/tmp/work.txt"),)) == (
        "intermediate!"
    )

    with pytest.raises(FileNotFoundError):
        _ = mount.dispatch("open", (_virtual("missing.md"), "r"))


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


def test_scratch_writes_are_capped_across_the_run(workspace: Path) -> None:
    # Written characters land on disk and stay there, so this is the one budget
    # a loop can genuinely exhaust.
    mount = _mount(workspace, writable=True)
    mount.max_scratch_chars = 10

    _ = mount.path_write_text(_virtual(".scratch/a.txt"), "x" * 8)
    with pytest.raises(MemoryError, match="`.scratch/`"):
        _ = mount.path_write_text(_virtual(".scratch/b.txt"), "y" * 8)


def test_documents_are_read_only_however_they_are_addressed(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)

    for act in (
        lambda: mount.path_write_text(_virtual("notes.md"), "new"),
        lambda: mount.path_unlink(_virtual("notes.md")),
        lambda: mount.path_open(_virtual("notes.md"), "w"),
        lambda: mount.path_rename(_virtual("notes.md"), _virtual("moved.md")),
        # A `..` segment resolves to the same document, so the refusal cannot
        # be spelled around.
        lambda: mount.path_write_text(_virtual("reports/../notes.md"), "new"),
    ):
        with pytest.raises(PermissionError):
            act()

    assert (workspace / "notes.md").read_text() == "alpha\n"


def test_scratch_state_is_written_where_the_run_may_write(workspace: Path) -> None:
    mount = _mount(workspace, writable=True)
    state = _virtual(".scratch/run/state.json")

    _ = mount.path_write_text(state, "{}")

    assert (workspace / ".scratch" / "run" / "state.json").read_text() == "{}"
    assert mount.path_read_text(state) == "{}"
    assert mount.path_is_file(state)

    mount.path_unlink(state)
    assert not mount.path_exists(state)


def test_scratch_write_needs_a_writable_span(workspace: Path) -> None:
    mount = _mount(workspace)

    with pytest.raises(PermissionError, match="chat mode"):
        _ = mount.path_write_text(_virtual(".scratch/state.json"), "{}")

    assert not (workspace / ".scratch").exists()
