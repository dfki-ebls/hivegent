"""Unit tests for text writes, edits, and index syncs through the changeset gateway.

The rows live in the shared in-memory index: a description is indexed where
it lies, while a text original's rewrite regenerates its markdown projection
through the upload conversion.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from exar.ingest import VISION_MEDIA_TYPES
from exar.memory import digest

from hivegent import workspace
from hivegent.pipelines import DEFAULT_CHUNKING, chunker_fingerprint
from hivegent.server.operations import reads
from hivegent.shared.text import content_hash
from hivegent.store import GroupScope, HostGrant, Workspace
from hivegent.tools.changes import (
    ChangeRefused,
    Changeset,
    Delete,
    Edit,
    Operation,
    TextEdit,
    Write,
)
from hivegent.tools.entries import ContentStat
from hivegent.workspace import Gateway, Root
from tests.helpers import FakeIndex, entry

_HELLO = content_hash("hello world")


@dataclass(slots=True)
class Space:
    """A personal and a group workspace over the in-memory index."""

    store: Workspace
    root: Path
    team: Path
    gateway: Gateway
    index: FakeIndex

    async def commit(self, *operations: Operation[str]) -> str:
        return await self.gateway.commit(Changeset(operations))

    def files(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def put(self, files: Mapping[str, str | bytes]) -> None:
        for name, content in files.items():
            path = self.root / name

            if name.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)

                continue

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode() if isinstance(content, str) else content)


@pytest.fixture()
def space(user_store: Workspace, index: FakeIndex) -> Space:
    team = HostGrant().workspace(GroupScope("team"))
    roots = user_store.workspace_dir(), team.workspace_dir()

    for path in roots:
        path.mkdir(parents=True, exist_ok=True)

    gateway = Gateway((Root.of(user_store), Root.of(team)))

    return Space(user_store, *roots, gateway, index)


def _edit(
    path: str,
    old: str,
    new: str,
    replace_all: bool = False,
    basis: str | None = None,
) -> Edit[str]:
    return Edit(f"~/{path}", (TextEdit(old, new, replace_all),), basis)


@pytest.mark.parametrize(
    ("files", "operation", "expected", "receipt"),
    [
        pytest.param({"doc.md": "hello world"}, _edit("doc.md", "hello", "hi"), {"doc.md": "hi world"}, "Replaced 1 occurrence", id="edit"),
        pytest.param({"doc.md": "foo foo foo"}, _edit("doc.md", "foo", "bar", True), {"doc.md": "bar bar bar"}, "Replaced 3 occurrences", id="edit all"),
        pytest.param({"doc.md": "hello world"}, _edit("doc.md", "hello", "hi", basis=_HELLO), {"doc.md": "hi world"}, "Replaced 1", id="edit with its hash"),
        pytest.param(
            {"doc.md": "a b a"},
            Edit("~/doc.md", (TextEdit("a", "x", replace_all=True), TextEdit("x b", "done"))),
            {"doc.md": "done x"},
            "Applied 2 edits with 3 replacements to '~/doc.md'.",
            id="edits in order",
        ),
        pytest.param({}, Write("~/a/b/new.md", "content"), {"a/b/new.md": "content"}, "Wrote 7 characters to '~/a/b/new.md'", id="write with parents"),
        pytest.param({}, Write("~/new.md", "body", "create"), {"new.md": "body"}, "Created", id="create"),
        pytest.param({"doc.md": "start"}, Write("~/doc.md", " end", "append"), {"doc.md": "start end"}, "Appended", id="append"),
        pytest.param({"doc.md": "end"}, Write("~/doc.md", "start ", "prepend"), {"doc.md": "start end"}, "Prepended", id="prepend"),
        pytest.param({"doc.md": "start"}, Write("~/doc.md", " end", "append", content_hash("start")), {"doc.md": "start end"}, "Appended", id="append with its hash"),
    ],
)  # fmt: skip
async def test_text_changes_land_and_report_the_canonical_path(
    space: Space,
    files: Mapping[str, str],
    operation: Operation[str],
    expected: dict[str, str],
    receipt: str,
) -> None:
    space.put(files)

    assert receipt in await space.commit(operation)
    assert space.files() == {name: text.encode() for name, text in expected.items()}


async def test_a_group_receipt_carries_the_group_prefix(space: Space) -> None:
    assert "'@team/notes.md'" in await space.commit(Write("@team/notes.md", "hi"))


_BINARY_NAMES = [f"notes{suffix}" for suffix in sorted(VISION_MEDIA_TYPES)]
_PLAIN = "plain text wearing a binary extension"


@pytest.mark.parametrize(
    ("files", "operation", "status", "match"),
    [
        pytest.param({"doc.md": "hello world"}, _edit("doc.md", "absent", "x"), 422, None, id="missing string"),
        pytest.param({"doc.md": "hi hi"}, _edit("doc.md", "hi", "yo"), 422, "appears 2 times", id="duplicate string"),
        pytest.param({}, _edit("nope.md", "a", "b"), 404, None, id="missing file"),
        pytest.param({"doc.md": "hello world"}, _edit("doc.md", "hello", "hi", basis="stale0000000"), 409, r"'~/doc\.md' changed since", id="stale edit"),
        pytest.param(
            {"doc.md": "a b"},
            Edit("~/doc.md", (TextEdit("a", "x"), TextEdit("missing", "y"))),
            422,
            "Edit 2 of 2: old_string not found",
            id="second edit fails",
        ),
        pytest.param({"fresh.md": "body"}, Write("~/fresh.md", "other", "create"), 409, None, id="create over a file"),
        pytest.param({}, Write("~/nope.md", "x", "append"), 404, None, id="append to nothing"),
        pytest.param({"doc.md": "start"}, Write("~/doc.md", " end", "append", "stale0000000"), 409, None, id="stale append"),
        pytest.param({}, Write("~/new.md", "body", basis="deadbeef0000"), 409, None, id="hash of a missing file"),
        pytest.param({"blocker.md": "in the way"}, Write("~/blocker.md/child.md", "nope"), 409, r"parent '~/blocker\.md' is a file", id="below a file"),
        pytest.param({}, Write("~/sheet.xlsx", "a,b"), 400, None, id="binary format from text"),
        pytest.param({}, Write("~/report.docx", "text"), 400, None, id="converted format from text"),
        pytest.param({"report.pdf": b"%PDF\x00\x01binary"}, Write("~/report.pdf", "text"), 422, None, id="binary original"),
        pytest.param({"notes.md": "hand written"}, Write("~/notes.ini", "x = 1"), 409, None, id="original over an entry"),
        *(pytest.param({name: _PLAIN}, Write(f"~/{name}", "replacement"), 422, None, id=name) for name in _BINARY_NAMES),
    ],
)  # fmt: skip
async def test_refused_changes_write_nothing(
    space: Space,
    files: Mapping[str, str | bytes],
    operation: Operation[str],
    status: int,
    match: str | None,
) -> None:
    space.put(files)
    before = space.files()

    with pytest.raises(ChangeRefused, match=match) as exc:
        await space.commit(operation)

    assert exc.value.status_code == status
    assert space.files() == before


@pytest.mark.parametrize("names", [("doc.txt", "doc.md"), ("doc.md", "doc.txt")])
async def test_an_original_and_its_projection_are_refused_in_either_order(
    space: Space, names: tuple[str, str]
) -> None:
    space.put({"doc.txt": "before", "doc.md": "before"})

    with pytest.raises(ChangeRefused, match="more than one change") as exc:
        await space.commit(*(Write(f"~/{name}", "after") for name in names))

    assert exc.value.status_code == 400
    assert set(space.files().values()) == {b"before"}


async def test_a_missed_old_string_shows_the_closest_text(space: Space) -> None:
    space.put({"doc.md": "intro\nrows = [r for r in data]\nend\n"})

    with pytest.raises(ChangeRefused, match="from line 2") as exc:
        await space.commit(_edit("doc.md", "rows = [r for r in date]", "x"))

    assert str(exc.value.detail).endswith("\nrows = [r for r in data]")


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252", "utf-16"])
async def test_editing_a_text_original_regenerates_its_projection(
    space: Space, encoding: str
) -> None:
    space.put({"settings.ini": "city = Köln\n".encode(encoding)})

    receipt = await space.commit(_edit("settings.ini", "Köln", "Berlin"))

    assert space.files()["settings.ini"] == b"city = Berlin\n"
    assert "city = Berlin" in space.files()["settings.md"].decode()
    assert "'~/settings.md' was regenerated" in receipt
    assert ("transcoded" in receipt) == (encoding != "utf-8")


@pytest.mark.parametrize(
    ("path", "content", "projected"),
    [
        ("diagram.svg", "<svg/>", "<svg/>"),
        ("data/rows.csv", "a,b\n1,2\n", "| 1 | 2 |"),
        ("conf/app.xml", "<a/>", "<a/>"),
    ],
)
async def test_writing_a_text_original_creates_it_and_its_projection(
    space: Space, path: str, content: str, projected: str
) -> None:
    await space.commit(Write(f"~/{path}", content))

    files = space.files()
    assert files[path] == content.encode()
    assert projected in files[path.rpartition(".")[0] + ".md"].decode()


async def test_rechunk_infers_a_rowless_entry_from_disk(space: Space) -> None:
    space.put({"photo.png": b"png", "photo.md": "caption"})

    await workspace.rechunk(space.store, "photo.md")

    [indexed] = space.index.indexed
    assert (indexed.entry_kind, indexed.origin, indexed.original_path) == (
        "image",
        "upload",
        "photo.png",
    )


@pytest.mark.parametrize(
    ("companions", "fresh_stat", "chunker", "changed", "row", "indexed"),
    [
        pytest.param(True, False, chunker_fingerprint(DEFAULT_CHUNKING), 1, ("doc.pdf", "doc.assets"), [], id="metadata only"),
        pytest.param(False, True, chunker_fingerprint(DEFAULT_CHUNKING), 0, (None, None), [], id="nothing moved"),
        pytest.param(False, True, "another chunker", 1, (None, None), ["doc.md"], id="another chunker"),
    ],
)  # fmt: skip
async def test_sync_reads_and_indexes_only_what_moved(
    space: Space,
    companions: bool,
    fresh_stat: bool,
    chunker: str,
    changed: int,
    row: tuple[str | None, str | None],
    indexed: list[str],
) -> None:
    space.put({"doc.md": "body"})

    if companions:
        space.put({"doc.pdf": b"%PDF", "doc.assets/": ""})

    stat = (
        ContentStat.from_path(space.root / "doc.md")
        if fresh_stat
        else ContentStat(mtime_ns=0, size=0, inode=0)
    )
    space.index.add(
        space.store, entry("doc"), digest=digest("body"), chunker=chunker, stat=stat
    )

    assert await workspace.sync_entries_from_disk(space.store, ["doc.md"]) == changed

    metadata = space.index.rows[space.store.store_key, "doc"].state.metadata
    assert (metadata.original_path, metadata.assets_dir) == row
    assert [e.description_path for e in space.index.indexed] == indexed


async def test_sync_leaves_a_hand_dropped_binary_inert(space: Space) -> None:
    space.put({"blob.bin": b"\x00\x01\x02"})

    assert await workspace.sync_entries_from_disk(space.store, ["blob.bin"]) == 0
    assert space.files() == {"blob.bin": b"\x00\x01\x02"}
    assert (space.index.rows, space.index.indexed) == ({}, [])


async def test_a_nested_asset_description_can_be_updated_and_listed(
    space: Space,
) -> None:
    space.put({"doc.assets/images/img.png": b"binary"})

    updated = await workspace.update_asset_description(
        space.store, "doc.md", "images/img.png", "nested description"
    )
    listed = reads.list_assets(space.store, "doc.md")

    assert (updated.name, updated.description_path) == (
        "images/img.png",
        "doc.assets/images/img.md",
    )
    assert [(asset.name, asset.description) for asset in listed.assets] == [
        ("images/img.png", "nested description")
    ]


async def test_an_asset_description_needs_its_asset(space: Space) -> None:
    space.put({"doc.assets/": ""})

    with pytest.raises(ChangeRefused) as exc:
        await Gateway((Root.of(space.store),), host=True).apply(
            Changeset((Write("~/doc.assets/img.md", "orphan"),))
        )

    assert exc.value.status_code == 404
    assert space.files() == {}


async def test_deleting_an_asset_description_keeps_the_asset_and_clears_the_row(
    space: Space,
) -> None:
    space.put({"doc.assets/img.png": b"binary", "doc.assets/img.md": "a description"})

    space.index.add(space.store, entry("doc.assets/img"))

    deleted = await workspace.delete_asset_description(space.store, "doc.md", "img.png")

    assert space.files() == {"doc.assets/img.png": b"binary"}
    assert (deleted.description, deleted.description_path) == ("", None)
    assert space.index.rows == {}


async def test_deleting_the_description_of_a_missing_asset_is_404(space: Space) -> None:
    space.put({"doc.assets/": ""})

    with pytest.raises(ChangeRefused) as exc:
        await workspace.delete_asset_description(space.store, "doc.md", "img.png")

    assert exc.value.status_code == 404
    assert space.index.outcomes == []


@pytest.mark.parametrize("committed", [True, False])
async def test_a_lost_commit_acknowledgement_leaves_the_files_as_the_rows_stand(
    space: Space, committed: bool
) -> None:
    space.put({"doc.md": "hello"})
    space.index.add(space.store, entry("doc"))
    space.index.drops = "committed" if committed else "rolled back"

    with pytest.raises(ConnectionError):
        await space.commit(Delete("~/doc.md"))

    assert ("doc.md" in space.files()) != committed
