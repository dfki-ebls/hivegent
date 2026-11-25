"""External sources: the confined client, sync planning and its deletion guard, and read-only visibility."""

from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hivegent import reconcile
from hivegent.config import ADMIN_ROLE, settings
from hivegent.config.integrations import SourceSettings
from hivegent.db import documents as db_documents
from hivegent.db import engine as db_engine
from hivegent.db import sources as db_sources
from hivegent.db.documents import EntryState
from hivegent.metadata import EntryMetadata
from hivegent.security import UnsafeUrlError, UrlPolicy, create_confined_client
from hivegent.shared.connectors import Delta, RemoteItem
from hivegent.sources import catalog, sync
from hivegent.store import (
    AccessDenied,
    HostGrant,
    SourceScope,
    User,
    split_scope_path,
)
from hivegent.tools.changes import Delete, Move, Write
from hivegent.tools.entries import Part
from hivegent.workspace import (
    Location,
    Root,
    RowChange,
    Upload,
    apply_changeset,
    directories,
)
from hivegent.workspace.changeset import apply as changeset_apply
from hivegent.workspace.documents import rechunk


def _item(path: str, version: str = "v1", id: str | None = None) -> RemoteItem:
    return RemoteItem(id=id or path, path=path, version=version)


async def test_a_source_client_reaches_only_its_hosts() -> None:
    policy = UrlPolicy(allow_hosts=("cloud.test",))

    async with create_confined_client(policy=policy, timeout=1, auth=None) as client:
        with pytest.raises(UnsafeUrlError):
            _ = await client.get("https://elsewhere.test/")


def test_a_plan_moves_renames_deletes_the_gone_and_skips_what_no_mirror_holds() -> None:
    known = [_item("a.md", id="1"), _item("b.txt", id="2"), _item("c.txt", id="3")]
    listing = [
        _item("docs/a.md", id="1"),
        _item("b.txt", "v2", id="2"),
        _item("b.md", id="4"),
        _item(".hidden/x.txt", id="5"),
    ]

    plan = sync.plan_sync(known, Delta(items=listing, listed=True))

    assert [(old.path, new.path) for old, new in plan.moves] == [("a.md", "docs/a.md")]
    assert [item.path for item in plan.downloads] == ["b.txt"]
    assert [item.path for item in plan.deletes] == ["c.txt"]
    assert sorted(plan.skipped) == [".hidden/x.txt", "b.md"]


def test_a_plan_follows_a_rename_chain_and_a_swap() -> None:
    known = [
        _item("a.md", id="A"),
        _item("b.md", id="B"),
        _item("x.md", id="X"),
        _item("y.md", id="Y"),
    ]
    listing = [
        _item("b.md", id="A"),
        _item("c.md", id="B"),
        _item("y.md", id="X"),
        _item("x.md", id="Y"),
    ]

    plan = sync.plan_sync(known, Delta(items=listing))

    assert {(old.path, new.path) for old, new in plan.moves} == {
        ("a.md", "b.md"),
        ("b.md", "c.md"),
        ("x.md", "y.md"),
        ("y.md", "x.md"),
    }
    assert plan.deletes == () and plan.skipped == ()


def test_a_removal_deletes_at_once_by_path_and_below_unbraked() -> None:
    known = [_item(f"docs/{n}.md") for n in range(20)] + [_item("c.md")]

    plan = sync.plan_sync(known, Delta(items=[], removed=["docs"]), max_sweep_share=0.0)

    assert len(plan.deletes) == 20 and plan.held == ()


def test_a_file_absent_from_a_complete_listing_is_deleted_at_once() -> None:
    known = [_item("a.md"), _item("b.md")]

    plan = sync.plan_sync(known, Delta(items=[_item("a.md")], listed=True))

    assert plan.deletes == (_item("b.md"),) and plan.held == ()


def test_an_incomplete_listing_deletes_only_its_tombstones() -> None:
    known = [_item("a.md"), _item("b.md"), _item("c.md")]
    delta = Delta(items=[_item("a.md")], removed=["c.md"], listed=True, refused=["x"])

    assert sync.plan_sync(known, delta).deletes == (_item("c.md"),)
    assert sync.plan_sync(known, replace(delta, refused=(), listed=False)).deletes == (
        _item("c.md"),
    )


def test_a_listed_file_the_mirror_no_longer_admits_is_lost_like_an_absent_one() -> None:
    known = [_item("a.md"), _item("b.md")]
    listing = [_item("a.md"), _item(".hidden/b.md", id="b.md")]

    assert sync.plan_sync(known, Delta(items=listing)).deletes == (_item("b.md"),)
    held = sync.plan_sync(known, Delta(items=listing), held=["b.md"]).held

    assert held == (_item("b.md"),)


def test_the_brake_holds_a_sweep_emptying_the_mirror_or_beyond_its_share() -> None:
    small = [_item("a.md"), _item("b.md")]
    large = [_item(f"{n}.md") for n in range(30)]

    def plan(
        known: list[RemoteItem], listing: list[RemoteItem], confirmed: bool = False
    ) -> sync.SyncPlan:
        return sync.plan_sync(
            known,
            Delta(items=listing, listed=True),
            max_sweep_share=0.5,
            confirmed=confirmed,
        )

    assert len(plan(small, []).held) == 2
    assert len(plan(small, small[:1]).deletes) == 1
    assert len(plan(large, large[:10]).held) == 20
    assert len(plan(large, large[:20]).deletes) == 10
    assert len(plan(small, [], confirmed=True).held) == 2


def test_held_files_stay_held_until_listed_again_or_confirmed() -> None:
    known = [_item("a.md"), _item("b.md")]
    held = ["a.md", "b.md"]
    nothing = Delta(items=[])

    plan = sync.plan_sync(known, nothing, held=held)

    assert plan.deletes == () and len(plan.held) == 2

    plan = sync.plan_sync(known, Delta(items=[_item("a.md")]), held=held)

    assert [item.id for item in plan.held] == ["b.md"]

    plan = sync.plan_sync(known, Delta(items=known, listed=True), held=held)

    assert plan.held == () and plan.deletes == ()

    plan = sync.plan_sync(known, nothing, held=held, confirmed=True)

    assert plan.deletes == () and len(plan.held) == 2

    plan = sync.plan_sync(
        known, Delta(items=[], listed=True), held=held, confirmed=True
    )

    assert len(plan.deletes) == 2 and plan.held == ()

    plan = sync.plan_sync(
        [*known, _item("c.md")], Delta(items=[], listed=True), held=held, confirmed=True
    )

    assert [item.id for item in plan.deletes] == ["a.md", "b.md"]
    assert [item.id for item in plan.held] == ["c.md"]


def test_a_file_moved_onto_the_stem_of_a_spared_one_is_lost_with_it() -> None:
    known = [_item("a.md", id="A"), _item("b.md", id="B")]
    delta = Delta(items=[_item("a.md", id="B")], listed=True)

    plan = sync.plan_sync(known, delta, held=["A"])

    assert plan.deletes == () and {item.id for item in plan.held} == {"A", "B"}


def test_a_tombstone_is_never_held() -> None:
    known = [_item(f"{n}.md") for n in range(30)]
    plan = sync.plan_sync(known, Delta(items=[], removed=["0.md"]), held=["0.md"])

    assert plan.deletes == (_item("0.md"),) and plan.held == ()


def test_a_plan_skips_sidecars_and_deletes_a_mirrored_one() -> None:
    sidecar = _item(Part("cars", "sidecar", ".csv").path)
    known = [_item("cars.csv"), sidecar]

    plan = sync.plan_sync(known, Delta(items=known, listed=True))

    assert (plan.downloads, plan.skipped, plan.deletes) == (
        (),
        (sidecar.path,),
        (sidecar,),
    )


class Memory:
    """A source whose files live in memory."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.refused: list[str] = []
        self.cursors: list[str | None] = []

    async def read(self, item: RemoteItem, /) -> bytes:
        return self.files[item.path]

    async def delta(self, cursor: str | None, /) -> Delta:
        self.cursors.append(cursor)

        return Delta(
            items=[_item(path) for path in self.files],
            cursor="next",
            listed=True,
            refused=self.refused,
        )


@pytest.fixture
def source(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[catalog.MirroredSource]:
    """The source "wiki", everyone its audience, with its state in memory."""
    _ = data_dir
    config = SourceSettings.model_validate(
        {"connector": "memory", "url": "https://cloud.test/dav/"}
    )
    monkeypatch.setitem(settings.sources, "wiki", config)
    built = catalog.MirroredSource.build(
        "wiki", config, {"memory": lambda _host: Memory()}
    )
    monkeypatch.setattr(catalog, "SOURCES", {"wiki": built})
    monkeypatch.setattr(sync, "SOURCES", {"wiki": built})
    yield built


DUE = db_sources.Due("sync", 0)

SESSION = AsyncSession()
"""What the stubbed gateway hands a commit's row writes, which the stubs ignore."""


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The source's items and cursor kept in memory, recording how each sync ended.

    The items change only through a commit's transaction.
    """
    kept: dict[str, Any] = {
        "items": {},
        "held": set(),
        "cursor": None,
        "finished": [],
        "stale": set(),
    }

    async def load_states() -> dict[str, db_sources.SourceState]:
        return {"wiki": db_sources.SourceState(cursor=kept["cursor"])}

    async def load_items(_source_id: str) -> list[RemoteItem]:
        return list(kept["items"].values())

    async def load_held(_source_id: str) -> set[str]:
        return kept["held"]

    async def set_held(_s: object, _source_id: str, ids: Sequence[str]) -> None:
        kept["held"] = set(ids)

    async def put_items(
        _s: object, _source_id: str, items: Sequence[RemoteItem]
    ) -> None:
        kept["items"].update((item.id, item) for item in items)

    async def drop_items(_s: object, _source_id: str, ids: Sequence[str]) -> None:
        for remote_id in ids:
            del kept["items"][remote_id]

    async def finish_sync(
        _source_id: str, _due: db_sources.Due, **ended: object
    ) -> None:
        kept["finished"].append(ended)

    async def drift(_store: object, _known: object) -> set[str]:
        return kept["stale"]

    for name, stub in {
        "load_states": load_states,
        "load_items": load_items,
        "load_held": load_held,
        "set_held": set_held,
        "put_items": put_items,
        "drop_items": drop_items,
        "finish_sync": finish_sync,
    }.items():
        monkeypatch.setattr(db_sources, name, stub)

    monkeypatch.setattr(sync, "_drift", drift)

    return kept


async def test_a_sync_downloads_new_files_as_synced_uploads(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(source.connector, Memory)
    source.connector.files.update({"a.md": b"# A", "b.txt": b"b"})
    landed: list[Upload] = []

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        assert host
        await record(SESSION)
        landed.extend(items)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert report.downloaded == 2 and report.failed == 0
    assert {upload.origin for upload in landed} == {"synced"}
    assert sorted(state["items"]) == ["a.md", "b.txt"]
    assert state["finished"][-1]["error"] is None


async def test_a_braked_sync_lands_the_rest_and_holds_only_the_deletions(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(source.connector, Memory)
    source.connector.files["new.md"] = b"# New"
    state["items"] = {f"{n}.md": _item(f"{n}.md") for n in range(30)}
    state["cursor"] = "kept"

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        assert host and all(isinstance(item, Upload) for item in items)
        await record(SESSION)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.deleted, report.held) == (1, 0, 30)
    assert len(state["items"]) == 31
    assert state["finished"][-1] == {"cursor": "next", "error": None}
    assert len(state["held"]) == 30

    report = await sync.sync_source(source, HostGrant(), db_sources.Due("confirm", 0))

    assert (report.deleted, report.held) == (30, 0)
    assert list(state["items"]) == ["new.md"] and state["held"] == set()


async def test_only_a_confirm_lists_in_full(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(source.connector, Memory)
    state["items"] = {f"{n}.md": _item(f"{n}.md") for n in range(30)}
    state["cursor"] = "kept"

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        assert host
        await record(SESSION)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    for request in ("sync", "sync", "confirm"):
        _ = await sync.sync_source(source, HostGrant(), db_sources.Due(request, 0))

    assert source.connector.cursors == ["kept", "kept", None]


async def test_a_refused_folder_lands_the_rest_deletes_nothing_and_names_it(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As rclone, a listing the source refused in part mirrors what it lists and deletes nothing missing."""
    assert isinstance(source.connector, Memory)
    source.connector.files["a.md"] = b"# A"
    source.connector.refused = ["private"]
    state["items"] = {"private/b.md": _item("private/b.md"), "c.md": _item("c.md")}
    state["cursor"] = "kept"

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        assert host and all(isinstance(item, Upload) for item in items)
        await record(SESSION)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.deleted, report.refused) == (1, 0, ["private"])
    assert sorted(state["items"]) == ["a.md", "c.md", "private/b.md"]
    assert state["finished"][-1]["cursor"] == "kept"
    assert "refused to list private" in state["finished"][-1]["error"]


async def test_a_sync_deletes_a_sidecar_before_it_moves_its_case_base(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sidecar gone from the source leaves first, so the move cannot carry it along."""
    store = HostGrant().workspace(SourceScope("wiki"))
    sidecar = Part("cars", "sidecar", ".csv").path
    state["items"] = {path: _item(path) for path in ("cars.csv", sidecar)}

    for path in ("cars.csv", sidecar):
        store.workspace_dir().joinpath(path).write_text("x")

    batches: list[list[str]] = []

    async def apply(
        items: Sequence[Delete[Location] | Move[Location]],
        *,
        host: bool,
        record: RowChange,
    ) -> None:
        assert host
        await record(SESSION)
        batches.append(
            [
                f"Delete {item.target.path}"
                if isinstance(item, Delete)
                else f"Move {item.source.path}"
                for item in items
            ]
        )

    monkeypatch.setattr(sync, "apply_changeset", apply)
    moved = (_item("cars.csv"), _item("fleet.csv", id="cars.csv"))

    _ = await sync._apply(
        source,
        store,
        sync.SyncPlan(moves=(moved,), deletes=(_item(sidecar),)),
        sync.Progress(),
    )

    assert batches == [[f"Delete {sidecar}"], ["Move cars.csv"]]


async def test_a_sync_bisects_a_failed_batch(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad file costs only its halves."""
    assert isinstance(source.connector, Memory)
    source.connector.files.update({f"{n}.md": b"# N" for n in range(4)})
    commits: list[int] = []

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        commits.append(len(items))

        if any(item.target.path == "3.md" for item in items):
            raise ValueError("unconvertible")

        await record(SESSION)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.failed) == (3, 1)
    assert [size for size in commits if size] == [4, 2, 2, 1, 1]


async def test_a_sync_downloads_again_what_the_mirror_lost_from_a_full_listing(
    source: catalog.MirroredSource,
    state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(source.connector, Memory)
    source.connector.files.update({"a.md": b"# A", "b.md": b"# B"})
    state["items"] = {path: _item(path) for path in source.connector.files}
    state["cursor"] = "kept"
    state["stale"] = {"b.md"}
    landed: list[str] = []

    async def apply(items: Sequence[Upload], *, host: bool, record: RowChange) -> None:
        await record(SESSION)
        landed.extend(item.target.path for item in items)

    monkeypatch.setattr(sync, "apply_changeset", apply)

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert landed == ["b.md"] and report.downloaded == 1
    assert source.connector.cursors == [None]


async def test_drift_is_a_missing_file_or_a_missing_or_unindexed_row(
    source: catalog.MirroredSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HostGrant().workspace(SourceScope(source.id))

    for path in ("a.pdf", "b.pdf", "d.pdf", "cars.csv"):
        store.workspace_dir().joinpath(path).write_text("x")

    known = [_item(path) for path in ("a.pdf", "b.pdf", "c.pdf", "d.pdf", "cars.csv")]
    metadata = EntryMetadata(
        entry_kind="user_markdown",
        stem_path="a",
        description_path="a.md",
        origin="synced",
        generated_by="user",
    )

    async def get_entry_states(_store: object) -> dict[str, EntryState]:
        return {
            path: EntryState(digest, None, None, metadata)
            for path, digest in {
                "a.md": "x",
                "b.md": None,
                "c.md": "x",
                "cars.md": "x",
            }.items()
        }

    monkeypatch.setattr(db_documents, "get_entry_states", get_entry_states)

    assert await sync._drift(store, known) == {"b.pdf", "c.pdf", "d.pdf"}


def test_a_source_is_seen_by_its_audience_and_written_by_nobody(
    source: catalog.MirroredSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = source
    monkeypatch.setattr(settings.sources["wiki"], "groups", ["team"])

    def mounted(user: User) -> dict[str, bool]:
        return {
            store.scope.prefix: store.writable
            for store in user.workspaces()
            if isinstance(store.scope, SourceScope)
        }

    assert mounted(User("a", read_groups=frozenset({"team"}))) == {"/mnt/wiki": False}
    assert mounted(User("b", write_groups=frozenset({"team"}))) == {"/mnt/wiki": False}
    assert mounted(User("c")) == {}
    assert split_scope_path("/mnt/wiki/docs/a.md") == ("/mnt/wiki", "docs/a.md")


async def test_a_mirror_is_changed_by_no_principal(
    source: catalog.MirroredSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin writing the global scope can neither write, wipe, nor rework a source's mirror."""
    _ = source

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def _no_rows(*_args: object, **_kwargs: object) -> dict[object, object]:
        return {}

    @asynccontextmanager
    async def _session() -> AsyncGenerator[object]:
        yield object()

    monkeypatch.setattr(db_engine, "session", _session)
    monkeypatch.setattr(changeset_apply, "chunk_and_index_document", _noop)
    monkeypatch.setattr(db_documents, "get_entries_metadata", _no_rows)
    monkeypatch.setattr(db_documents, "get_entry_metadata", _noop)
    admin = User("root", roles=frozenset({ADMIN_ROLE}))

    with pytest.raises(AccessDenied):
        _ = admin.workspace("/mnt/wiki", writable=True)

    mirror = admin.workspace("/mnt/wiki")
    mirror.workspace_dir().joinpath("a.md").write_text("# A")

    for change in (
        apply_changeset((Write(Location(Root.of(mirror), "a.md"), "# B"),)),
        directories.delete_all(mirror),
        rechunk(mirror, "a.md"),
    ):
        with pytest.raises(AccessDenied):
            await change

    assert (mirror.path / "a.md").read_text() == "# A"


def test_a_source_names_only_available_pipelines() -> None:
    config = SourceSettings.model_validate(
        {"connector": "memory", "url": "https://cloud.test/", "chunking": "nope"}
    )

    with pytest.raises(ValueError, match="sources.wiki.chunking"):
        _ = catalog.MirroredSource.build(
            "wiki", config, {"memory": lambda _host: Memory()}
        )


def test_the_reconcile_removes_mirrored_files_no_item_names(tmp_path: Path) -> None:
    files = ("a.pdf", "a.md", "a.assets/x.png", "stray.pdf", "stray.md")

    for path in files:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x")

    assert reconcile._sweep(tmp_path, ["a.pdf"]) == 2
    assert sorted(
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    ) == ["a.assets/x.png", "a.md", "a.pdf"]


async def test_the_boot_keeps_the_mirror_of_a_source_holding_lost_files(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its mirror is the only copy of what its source lost, so a configuration that dropped it by mistake loses nothing."""

    async def prune(
        configured: Sequence[str], *, keep_held: bool
    ) -> tuple[list[str], list[str]]:
        return (["gone"], ["held"]) if keep_held else (["gone", "held"], [])

    monkeypatch.setattr(db_sources, "prune_sources", prune)
    monkeypatch.setattr(sync, "SOURCES", {})
    mirrors = data_dir / "sources"

    for name in ("gone", "held"):
        (mirrors / name).mkdir(parents=True)

    await sync.prune_sources()
    assert [mirror.name for mirror in mirrors.iterdir()] == ["held"]

    await sync.prune_sources(keep_held=False)
    assert not any(mirrors.iterdir())
