"""Unit tests for moves, deletes, and batches through the changeset gateway.

Moves are planned and validated before any rename, an existing directory
destination means move-into, SQL moves are scoped to exactly the entry, the
items of one changeset apply at once, and a failure anywhere restores every
file and commits no row.  The SQL layer is the shared in-memory fake.
"""

import os
from collections.abc import Mapping
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from cbrkit.io import COMPANION_SUFFIXES
from pydantic import ValidationError

from hivegent import workspace, workspace_events
from hivegent.jobs import FeedReady
from hivegent.server.models import ChangesRequest, MoveDestination
from hivegent.server.routes import documents as documents_routes
from hivegent.store import GroupScope, HostGrant, User, Workspace
from hivegent.tools.base import SearchPath
from hivegent.tools.changes import (
    ChangeRefused,
    Changeset,
    ChangesetSummary,
    CreateDir,
    Delete,
    FileDiff,
    Move,
    Operation,
    PathMove,
    WorkspaceChanged,
    Write,
)
from hivegent.tools.changeset import stage_changes
from hivegent.tools.entries import (
    ContentStat,
    EntryPaths,
    Part,
)
from hivegent.tools.workspace_os import WORKSPACE_MOUNT, WorkspaceOS
from hivegent.workspace import Gateway, Root
from tests.helpers import LIMITS, FakeIndex, entry

_USER = User(id="testuser")
_LAYOUT = {"a.md": "a", "b.md": "b", "dir/c.md": "dir/c"}
_PARK = ".changeset-park/"
_GROUP = "@team/"


@dataclass(slots=True)
class Ws:
    """A personal and a group workspace on disk, one gateway over both, and the fake rows."""

    store: Workspace
    group_store: Workspace
    root: Path
    group: Path
    index: FakeIndex
    gateway: Gateway

    def put(self, files: Mapping[str, str | bytes]) -> None:
        """Write *files*, a key ending in ``/`` being a directory and one leading with ``@team/`` the group's.

        Every description gets a row, which :meth:`row` refines.
        """
        for name, content in files.items():
            base, local = (
                (self.group, name.removeprefix(_GROUP))
                if name.startswith(_GROUP)
                else (self.root, name)
            )
            path = base / local

            if name.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)

                continue

            path.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)

            if local.endswith(".md") and ".assets/" not in local:
                self.row(local.removesuffix(".md"), group=base is self.group)

    def row(
        self, stem: str, original_suffix: str | None = None, *, group: bool = False
    ) -> None:
        self.index.add(
            self.group_store if group else self.store, entry(stem, original_suffix)
        )

    def tree(self) -> dict[str, str]:
        """Every file by canonical-less path with its text, and every empty directory with a trailing slash."""
        found: dict[str, str] = {}

        for prefix, base in (("", self.root), (_GROUP, self.group)):
            for path in sorted(base.rglob("*")):
                key = prefix + path.relative_to(base).as_posix()

                if path.is_file():
                    found[key] = path.read_text(errors="replace")
                elif not any(path.iterdir()):
                    found[f"{key}/"] = ""

        return found

    async def apply(self, *operations: Operation[str]) -> workspace.AppliedChangeset:
        return await self.gateway.apply(Changeset(operations))

    async def plan(self, *operations: Operation[str]) -> workspace.PlannedChangeset:
        return await self.gateway.plan(Changeset(operations))


@pytest.fixture()
def ws(user_store: Workspace, index: FakeIndex) -> Ws:
    group_store = HostGrant().workspace(GroupScope("team"))
    roots = user_store.workspace_dir(), group_store.workspace_dir()

    for path in roots:
        path.mkdir(parents=True, exist_ok=True)

    gateway = Gateway((Root.of(user_store), Root.of(group_store)))

    return Ws(user_store, group_store, *roots, index, gateway)


@pytest.fixture()
def layout(ws: Ws) -> Ws:
    ws.put(_LAYOUT)

    return ws


def _move(src: str, dst: str) -> Move[str]:
    return Move(f"~/{src}", f"~/{dst}")


def _delete(path: str) -> Delete[str]:
    return Delete(f"~/{path}")


@pytest.mark.parametrize(
    ("files", "operations", "status", "match"),
    [
        pytest.param({}, (_move("a.md", "x.md"), _move("a.md", "y.md")), 400, "more than one change", id="source twice"),
        pytest.param({}, (_move("a.md", "x.md"), _move("b.md", "x.md")), 400, "more than one change", id="destination twice"),
        pytest.param({}, (_move("a.md", "x.md"), _delete("a.md")), 400, "more than one change", id="moved and deleted"),
        pytest.param({}, (_move("a.md", "b.md"),), 409, "already exists", id="onto an entry"),
        pytest.param({}, (_move("dir", "moved"), _move("a.md", "moved/c.md")), 409, "already exists", id="into a moved directory"),
        pytest.param({}, (Write("~/x", "1"), Write("~/x/y.md", "2")), 409, "'~/x' is a file", id="file and directory"),
        pytest.param({}, (_move("a.md", "x.md"), Write("~/x.txt", "x")), 409, "already exists", id="original onto a moved entry"),
        pytest.param({}, (Write("~/b.txt", "x"),), 409, "already exists", id="original onto an entry"),
        pytest.param({"b.pdf": b"%PDF"}, (_move("a.md", "b.md"),), 409, "already exists", id="orphan original"),
        pytest.param({"b.assets/": ""}, (_move("a.md", "b.md"),), 409, "already exists", id="orphan assets"),
        pytest.param({"data": b"bin", "data.md": "d", "blocked/data/": ""}, (_move("data.md", "blocked/data.md"),), 409, "already exists", id="blocked original"),
        pytest.param({"@team/a.md": "theirs"}, (Move("~/a.md", "@team/a.md"),), 409, "already exists", id="occupied group destination"),
        pytest.param({}, (_move("dir", "dir/sub"),), 400, None, id="into own subtree"),
        pytest.param({}, (_delete(""),), 400, None, id="workspace root"),
        pytest.param({}, (Delete("~/a.md", expect="dir"),), 404, "Directory not found", id="wrong kind"),
        pytest.param({}, (_move("a.md", "x.md"), Delete("~/dir", expect="entry")), 404, "~/dir", id="folder as an entry"),
        pytest.param({}, (CreateDir("~/a.md"),), 409, None, id="directory over a file"),
        pytest.param({"report.assets/": ""}, (CreateDir("~/x.assets"),), 400, None, id="new assets directory"),
        pytest.param({"report.assets/": ""}, (_move("report.assets", "elsewhere"),), 400, None, id="assets directory moved"),
        pytest.param({"cars.csv": "1", "cars.csv.cbrkit.json": "{}"}, (Write("~/cars.csv.cbrkit.json", "{}"),), 400, None, id="sidecar written"),
        pytest.param({"cars.csv": "1", "cars.csv.cbrkit.json": "{}"}, (_move("cars.csv.cbrkit.json", "x.cbrkit.json"),), 400, None, id="sidecar moved"),
        pytest.param({"afile": "x"}, (Write("~/afile/doc.md", "hello"),), 409, None, id="below a file"),
    ],
)  # fmt: skip
async def test_refused_changes_leave_everything_as_it_was(
    layout: Ws,
    files: Mapping[str, str | bytes],
    operations: tuple[Operation[str], ...],
    status: int,
    match: str | None,
) -> None:
    layout.put(files)
    before = layout.tree()

    with pytest.raises(ChangeRefused, match=match) as exc:
        await layout.apply(*operations)

    assert exc.value.status_code == status
    assert layout.tree() == before
    assert layout.index.outcomes == []


@pytest.mark.parametrize(
    ("stem", "suffix"), [("notes", ".pdf"), ("a.tar", ".gz")], ids=["plain", "dotted"]
)
async def test_an_entry_moves_whole_and_leaves_a_same_named_directory(
    ws: Ws, stem: str, suffix: str
) -> None:
    ws.put({f"{stem}.md": "desc", f"{stem}{suffix}": b"%PDF", f"{stem}/inner.md": "i"})
    ws.row(stem, suffix)

    await ws.apply(_move(f"{stem}.md", f"archive/{stem}.md"))

    assert ws.tree() == {
        f"archive/{stem}.md": "desc",
        f"archive/{stem}{suffix}": "%PDF",
        f"{stem}/inner.md": "i",
    }
    assert ws.index.moved() == [("move_document", stem, f"archive/{stem}")]


async def test_an_existing_directory_destination_is_moved_into(ws: Ws) -> None:
    ws.put(
        {"report.md": "r", "report.pdf": b"%PDF", "images/x.md": "x", "archive/": ""}
    )
    ws.row("report", ".pdf")

    await ws.apply(_move("report.md", "archive"), _move("images", "archive"))

    assert ws.tree() == {
        "archive/report.md": "r",
        "archive/report.pdf": "%PDF",
        "archive/images/x.md": "x",
    }
    assert ws.index.moved() == [
        ("move_document", "report", "archive/report"),
        ("move_subtree", "images", "archive/images"),
    ]


async def test_entries_and_directories_relocate_between_workspaces(ws: Ws) -> None:
    ws.put(
        {
            "report.md": "[a](report.assets/a.png)",
            "report.pdf": b"%PDF",
            "report.assets/a.png": b"img",
            "shared/x.md": "x",
        }
    )
    ws.row("report", ".pdf")

    await ws.apply(
        Move("~/report.md", "@team/report.md"), Move("~/shared", "@team/shared")
    )

    assert ws.tree() == {
        "@team/report.md": "[a](report.assets/a.png)",
        "@team/report.pdf": "%PDF",
        "@team/report.assets/a.png": "img",
        "@team/shared/x.md": "x",
    }
    assert [(name, *stores) for name, _src, _dst, *stores in ws.index.moves] == [
        ("move_document", "user:testuser", "group:team"),
        ("move_subtree", "user:testuser", "group:team"),
        ("move_subtree", "user:testuser", "group:team"),
    ]


@pytest.mark.parametrize(
    ("files", "path", "left"),
    [
        pytest.param({"keep/doc.md": "body"}, "keep/doc.md", {"keep/": ""}, id="last file keeps its directory"),
        pytest.param({"loose.bin": b"\x00"}, "loose.bin", {}, id="stray original"),
        pytest.param({"notes.md": "b", "notes.pdf": b"%PDF"}, "notes.md", {}, id="original the row never recorded"),
        pytest.param({"cars.csv": "p", "cars.csv.cbrkit.json": "{}"}, "cars.csv.cbrkit.json", {"cars.csv": "p"}, id="sidecar alone"),
    ],
)  # fmt: skip
async def test_a_delete_takes_what_the_filesystem_groups_with_the_entry(
    ws: Ws, files: Mapping[str, str | bytes], path: str, left: dict[str, str]
) -> None:
    ws.put(files)

    await ws.apply(_delete(path))

    assert ws.tree() == left


@pytest.mark.parametrize(
    ("operations", "expected"),
    [
        # A swap and a rotation, which no order of single moves can make.
        ((_move("a.md", "b.md"), _move("b.md", "a.md")), {"a.md": "b", "b.md": "a", "dir/c.md": "dir/c"}),
        ((_move("a.md", "b.md"), _move("b.md", "dir/c.md"), _move("dir/c.md", "a.md")), {"a.md": "dir/c", "b.md": "a", "dir/c.md": "b"}),
        # A chain, and a move onto a path the same call deletes.
        ((_move("a.md", "b.md"), _move("b.md", "x.md")), {"b.md": "a", "x.md": "b", "dir/c.md": "dir/c"}),
        ((_move("a.md", "b.md"), _delete("b.md")), {"b.md": "a", "dir/c.md": "dir/c"}),
        # Moves and deletes inside a directory another move takes.
        ((_move("dir", "moved"), _move("dir/c.md", "c.md")), {"a.md": "a", "b.md": "b", "c.md": "dir/c", "moved/": ""}),
        ((_move("dir", "moved"), _delete("dir/c.md")), {"a.md": "a", "b.md": "b", "moved/": ""}),
        ((_move("a.md", "dir/a.md"), _move("dir", "moved")), {"b.md": "b", "dir/a.md": "a", "moved/c.md": "dir/c"}),
        ((_move("a.md", "A.md"),), {"A.md": "a", "b.md": "b", "dir/c.md": "dir/c"}),
        ((CreateDir("~/new/deep"),), {**_LAYOUT, "new/deep/": ""}),
        # A delete frees the stem a new original claims.
        ((_delete("b.md"), Write("~/b.txt", "x")), {"a.md": "a", "dir/c.md": "dir/c", "b.txt": "x", "b.md": "```txt\nx\n```\n"}),
    ],
)  # fmt: skip
async def test_items_apply_at_once(
    layout: Ws, operations: tuple[Operation[str], ...], expected: dict[str, str]
) -> None:
    await layout.apply(*operations)

    assert layout.tree() == expected
    # Every move keeps its row, a swap included.
    assert len(layout.index.moved()) == sum(isinstance(o, Move) for o in operations)


@pytest.mark.usefixtures("folded_case")
async def test_a_case_insensitive_filesystem_sees_one_destination(layout: Ws) -> None:
    with pytest.raises(ChangeRefused, match="more than one change"):
        await layout.apply(_move("a.md", "X.md"), _move("b.md", "x.md"))


async def test_a_write_follows_the_move_that_carries_its_file(layout: Ws) -> None:
    """What a sandbox diff produces for a document renamed and then edited."""
    await layout.apply(
        Write("~/archive/a.md", "edited"),
        _move("a.md", "archive/a.md"),
        _move("dir", "moved"),
        Write("~/moved/c.md", "also edited"),
    )

    assert layout.tree() == {
        "archive/a.md": "edited",
        "b.md": "b",
        "moved/c.md": "also edited",
    }
    assert layout.index.moved() == [
        ("move_document", "a", "archive/a"),
        ("move_subtree", "dir", "moved"),
    ]


async def test_a_failed_row_change_restores_every_file(layout: Ws) -> None:
    before = layout.tree()
    layout.index.failing = {"b"}

    with pytest.raises(RuntimeError, match="row failed to move"):
        await layout.apply(
            _move("a.md", "archive/a.md"),
            _move("b.md", "archive/b.md"),
            _delete("dir"),
        )

    assert layout.index.outcomes == ["rolled back"]
    assert layout.tree() == before


async def test_a_replaced_file_fails_its_basis_despite_its_stat(layout: Ws) -> None:
    """A same-size replacement with the old mtime is a new inode."""
    live = layout.root / "a.md"
    seen = ContentStat.from_path(live)
    assert seen is not None
    other = layout.root / "other"
    other.write_text("z")
    os.utime(other, ns=(seen.mtime_ns, seen.mtime_ns))
    other.replace(live)

    with pytest.raises(ChangeRefused) as exc:
        await layout.apply(Delete("~/a.md", seen))

    assert exc.value.status_code == 409


@pytest.mark.parametrize("operation", ["delete", "move"])
@pytest.mark.parametrize("change", ["add", "edit", "replace"])
async def test_staged_directories_reject_changed_contents_or_kind(
    layout: Ws,
    user_store: Workspace,
    operation: Literal["delete", "move"],
    change: Literal["add", "edit", "replace"],
) -> None:
    root = SearchPath.of(path=layout.root, target=user_store.scope.prefix, implied=True)
    mount = WorkspaceOS(paths=(root,), writable=(root,), environ={}, limits=LIMITS)
    directory = WORKSPACE_MOUNT / "~/dir"

    if operation == "delete":
        mount.path_unlink(directory / "c.md")
        mount.path_rmdir(directory)
    else:
        mount.path_rename(directory, WORKSPACE_MOUNT / "~/moved")

    staged = stage_changes(mount)
    assert staged is not None
    await layout.gateway.plan(staged)

    match change:
        case "add":
            (layout.root / "dir/new.md").write_text("new")
        case "edit":
            (layout.root / "dir/c.md").write_text("updated")
        case "replace":
            (layout.root / "dir").rename(layout.root / "saved")
            (layout.root / "dir").write_text("replacement")

    with pytest.raises(ChangeRefused) as exc:
        await layout.gateway.apply(staged)

    assert exc.value.status_code == 409
    assert (layout.root / "dir").exists()
    assert not (layout.root / "moved").exists()
    assert layout.index.outcomes == []


async def test_plan_summarizes_what_a_person_approves(layout: Ws) -> None:
    layout.put({"b.pdf": b"%PDF"})

    planned = await layout.plan(
        _move("a.md", "b.md"),
        _delete("b.md"),
        _move("dir", "moved"),
        Write("~/moved/c.md", "C"),
        Write("~/new.md", "x\n"),
        CreateDir("~/empty"),
    )

    assert planned.summary == ChangesetSummary(
        creates=(
            FileDiff("~/new.md", "--- /dev/null\n+++ ~/new.md\n@@ -0,0 +1 @@\n+x\n"),
        ),
        updates=(
            FileDiff(
                "~/moved/c.md",
                "--- ~/moved/c.md\n+++ ~/moved/c.md\n@@ -1 +1 @@\n-dir/c\n"
                "\\ No newline at end of file\n+C\n\\ No newline at end of file\n",
            ),
        ),
        moves=(
            PathMove("~/a.md", "~/b.md", replaces=True),
            PathMove("~/dir", "~/moved", is_dir=True),
        ),
        deletes=("~/b.md", "~/b.pdf"),
        mkdirs=("~/empty",),
    )


async def test_applied_paths_reach_every_client_of_the_agent(
    layout: Ws, user_store: Workspace
) -> None:
    """Every client following the store follows the move to where it resolved, from one event."""
    follows = {user_store.store_key: user_store.scope.prefix}

    feed = workspace_events.manager.subscribe(["testuser"], follows)

    async with aclosing(feed) as events:
        assert isinstance(await anext(events), FeedReady)
        applied = await layout.apply(Move("~/a.md", "~/dir"), _delete("b.md"))
        event = await anext(events)

    assert applied.changed == WorkspaceChanged(
        scopes=("~",),
        moves=(PathMove("~/a.md", "~/dir/a.md"),),
        deletes=("~/b.md",),
    )
    assert event == applied.changed


@pytest.mark.parametrize("delete", [False, True])
async def test_every_companion_follows_its_subject(ws: Ws, delete: bool) -> None:
    """An entry and a folder case base move, rename, and go with every part they have."""
    entry = EntryPaths(
        "cars",
        frozenset({"description", "original", "assets", *COMPANION_SUFFIXES}),
        ".csv",
    )
    tree = EntryPaths.tree("flows", COMPANION_SUFFIXES)

    for path in (*entry.files, *tree.files):
        if Part.of(path).kind == "assets" or path == "flows":
            ws.put({f"{path}/a.json": "{}"})
        else:
            ws.put({path: "{}"})

    ws.row("cars", ".csv")

    if delete:
        await ws.apply(_delete("cars.md"), _delete("flows"))
        landed: tuple[str, ...] = ()
    else:
        await ws.apply(
            _move("cars.md", "archive/fleet.md"), _move("flows", "archive/graphs")
        )
        landed = (*entry.at("archive/fleet").files, *tree.at("archive/graphs").files)

    assert not [p for p in (*entry.files, *tree.files) if (ws.root / p).exists()]
    assert [p for p in landed if (ws.root / p).exists()] == list(landed)
    assert len(landed) == (0 if delete else 2 * len(COMPANION_SUFFIXES) + 4)
    # A renamed description points at its renamed assets, so its chunks follow.
    assert [e.description_path for e in ws.index.indexed] == (
        [] if delete else ["archive/fleet.md"]
    )


class TestChangesRoute:
    """``POST /changes`` sends every operation of a request through one changeset."""

    async def test_mixed_batch_applies_at_once(self, layout: Ws) -> None:
        request = ChangesRequest.model_validate(
            {
                "operations": [
                    {"kind": "move", "source": "~/a.md", "destination": "~/x.md"},
                    {"kind": "move", "source": "~/dir", "destination": "~/moved"},
                    {"kind": "delete", "path": "~/b.md", "expect": "entry"},
                    {"kind": "mkdir", "path": "~/new"},
                ]
            }
        )

        await documents_routes.apply_changes(request, _USER)

        assert layout.tree() == {"x.md": "a", "moved/c.md": "dir/c", "new/": ""}
        assert layout.index.moved() == [
            ("move_document", "a", "x"),
            ("move_subtree", "dir", "moved"),
        ]
        assert layout.index.stems(layout.store) == ["moved/c", "x"]
        assert layout.index.outcomes == ["committed"]

    @pytest.mark.parametrize(
        "operations",
        [
            [],
            [{"kind": "copy", "source": "~/a.md", "destination": "~/b.md"}],
            [{"kind": "delete", "path": "~/a.md"}],
        ],
    )
    def test_rejects_invalid_requests(self, operations: list[dict[str, str]]) -> None:
        with pytest.raises(ValidationError):
            ChangesRequest.model_validate({"operations": operations})

    async def test_single_item_shortcuts_go_through_the_changeset(
        self, layout: Ws
    ) -> None:
        """Each shortcut is one operation of ``POST /changes``, kind checks included."""
        await documents_routes.move_document(
            "~/a.md", MoveDestination(destination="~/x.md"), _USER
        )

        with pytest.raises(ChangeRefused) as exc:
            await documents_routes.delete_document("~/dir", _USER)

        assert exc.value.status_code == 404
        await documents_routes.delete_directory("~/dir", _USER)
        await documents_routes.create_directory("~/new", _USER)
        # A directory that exists is what a repeated PUT asked for.
        await documents_routes.create_directory("~/new", _USER)

        assert layout.tree() == {"x.md": "a", "b.md": "b", "new/": ""}
        assert layout.index.moved() == [("move_document", "a", "x")]
