"""External sources: the confined client, sync planning and its deletion guard, and read-only visibility."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from hivegent import reconcile
from hivegent.config import ADMIN_ROLE, settings
from hivegent.config.integrations import SourceSettings
from hivegent.db import sources as db_sources
from hivegent.shared.connectors import Delta, RemoteItem
from hivegent.sources import catalog, sync
from hivegent.store import (
    AccessDenied,
    HostGrant,
    SourceScope,
    User,
    Workspace,
    split_scope_path,
)
from hivegent.tools.changes import Write
from hivegent.tools.entries import Part
from hivegent.workspace import Location, Root, apply_changeset, directories
from hivegent.workspace.documents import rechunk
from tests.helpers import FakeIndex, entry


def _item(path: str, version: str = "v1", id: str | None = None) -> RemoteItem:
    return RemoteItem(id=id or path, path=path, version=version)


def _ids(items: Sequence[RemoteItem]) -> list[str]:
    return [item.id for item in items]


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
    assert plan.held == ()
    assert sorted(plan.skipped) == [".hidden/x.txt", "b.md"]


def test_a_plan_follows_a_rename_chain_and_a_swap() -> None:
    known = [_item(f"{path}.md", id=path.upper()) for path in ("a", "b", "x", "y")]
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


def test_a_removal_deletes_by_path_and_below_unbraked_and_is_never_held() -> None:
    known = [_item(f"docs/{n}.md") for n in range(20)] + [_item("c.md")]
    delta = Delta(items=[], removed=["docs"])

    plan = sync.plan_sync(known, delta, held=["docs/0.md"], max_sweep_share=0.0)

    assert len(plan.deletes) == 20 and plan.held == ()


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
    assert sync.plan_sync(known, Delta(items=listing), held=["b.md"]).held == (
        _item("b.md"),
    )


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


@pytest.mark.parametrize(
    ("delta", "confirmed", "deletes", "held"),
    [
        (Delta(items=[]), False, [], ["a.md", "b.md"]),
        (Delta(items=[_item("a.md")]), False, [], ["b.md"]),
        (Delta(items=[_item(f"{n}.md") for n in "abc"], listed=True), False, [], []),
        (Delta(items=[]), True, [], ["a.md", "b.md"]),
        (Delta(items=[], listed=True), True, ["a.md", "b.md"], ["c.md"]),
    ],
)
def test_held_files_stay_held_until_listed_again_or_confirmed(
    delta: Delta, confirmed: bool, deletes: list[str], held: list[str]
) -> None:
    known = [_item(f"{n}.md") for n in "abc"]

    plan = sync.plan_sync(known, delta, held=["a.md", "b.md"], confirmed=confirmed)

    assert (_ids(plan.deletes), _ids(plan.held)) == (deletes, held)


def test_a_file_moved_onto_the_stem_of_a_spared_one_is_lost_with_it() -> None:
    known = [_item("a.md", id="A"), _item("b.md", id="B")]
    delta = Delta(items=[_item("a.md", id="B")], listed=True)

    plan = sync.plan_sync(known, delta, held=["A"])

    assert plan.deletes == () and set(_ids(plan.held)) == {"A", "B"}


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

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
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
def memory() -> Memory:
    return Memory()


@pytest.fixture
def source(
    request: pytest.FixtureRequest,
    data_dir: Path,
    memory: Memory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[catalog.MirroredSource]:
    """The source "wiki", everyone its audience unless a test parametrizes its groups."""
    _ = data_dir
    config = SourceSettings.model_validate(
        {
            "connector": "memory",
            "url": "https://cloud.test/dav/",
            "groups": getattr(request, "param", []),
        }
    )
    monkeypatch.setitem(settings.sources, "wiki", config)
    built = catalog.MirroredSource.build(
        "wiki", config, {"memory": lambda _host: memory}
    )
    monkeypatch.setattr(catalog, "SOURCES", {"wiki": built})
    yield built


DUE = db_sources.Due("sync", 0)


@dataclass(slots=True)
class State:
    """The sources in memory, as :mod:`hivegent.db.sources` keeps them, recording how each sync ended.

    Its rows change only through the transaction of a commit, which fails for a
    file of :attr:`rejected`, and :attr:`puts` counts the files each one carried.
    """

    store: Workspace
    index: FakeIndex
    items: dict[str, RemoteItem] = field(default_factory=dict[str, RemoteItem])
    held: set[str] = field(default_factory=set[str])
    cursor: str | None = None
    finished: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    rejected: set[str] = field(default_factory=set[str])
    puts: list[int] = field(default_factory=list[int])
    stored: set[str] = field(default_factory=set[str])
    holding: set[str] = field(default_factory=set[str])

    def mirror(self, *paths: str) -> None:
        """Mirror *paths* as items already synced, from the cursor ``kept``, with their files and indexed rows."""
        self.items = {path: _item(path) for path in paths}
        self.cursor = "kept"

        for path in paths:
            file = self.store.workspace_dir() / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(f"# {path}")
            self.index.add(self.store, entry(Part.of(path).stem_path), digest="indexed")

    async def load_states(self) -> dict[str, db_sources.SourceState]:
        return {"wiki": db_sources.SourceState(cursor=self.cursor)}

    async def load_items(self, _source_id: str) -> list[RemoteItem]:
        return list(self.items.values())

    async def load_held(self, _source_id: str) -> set[str]:
        return self.held

    async def set_held(self, _s: object, _source_id: str, ids: Sequence[str]) -> None:
        self.held = set(ids)

    async def put_items(
        self, _s: object, _source_id: str, items: Sequence[RemoteItem]
    ) -> None:
        self.puts.append(len(items))

        if self.rejected & {item.id for item in items}:
            raise ValueError("unconvertible")

        self.items.update((item.id, item) for item in items)

    async def drop_items(self, _s: object, _source_id: str, ids: Sequence[str]) -> None:
        for remote_id in ids:
            del self.items[remote_id]

    async def finish_sync(
        self, _source_id: str, _due: db_sources.Due, **ended: object
    ) -> None:
        self.finished.append(ended)

    async def prune_sources(
        self, configured: Iterable[str], *, keep_held: bool
    ) -> tuple[list[str], list[str]]:
        unconfigured = self.stored - set(configured)
        gone = sorted(unconfigured - (self.holding if keep_held else set[str]()))
        self.stored -= set(gone)

        return gone, sorted(unconfigured - set(gone))


@pytest.fixture
def state(
    source: catalog.MirroredSource, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> State:
    """The state of "wiki", whose items change only through a commit's transaction."""
    kept = State(HostGrant().workspace(SourceScope(source.id)), index)

    for name in (
        "load_states",
        "load_items",
        "load_held",
        "set_held",
        "put_items",
        "drop_items",
        "finish_sync",
        "prune_sources",
    ):
        monkeypatch.setattr(db_sources, name, getattr(kept, name))

    return kept


async def test_a_sync_downloads_new_files_as_synced_uploads(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    memory.files.update({"a.md": b"# A", "b.txt": b"b"})

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert report.downloaded == 2 and report.failed == 0
    assert {e.origin for e in state.index.indexed} == {"synced"}
    assert sorted(state.items) == ["a.md", "b.txt"]
    assert state.finished[-1]["error"] is None


async def test_a_braked_sync_lands_the_rest_and_holds_only_the_deletions(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    memory.files["new.md"] = b"# New"
    state.mirror(*(f"{n}.md" for n in range(30)))

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.deleted, report.held) == (1, 0, 30)
    assert len(state.items) == 31 and len(state.held) == 30
    assert state.finished[-1] == {"cursor": "next", "error": None}

    report = await sync.sync_source(source, HostGrant(), db_sources.Due("confirm", 0))

    assert (report.deleted, report.held) == (30, 0)
    assert list(state.items) == ["new.md"] and state.held == set()
    assert state.index.stems(state.store) == ["new"]


async def test_only_a_confirm_lists_in_full(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    state.mirror("a.md")

    for request in ("sync", "sync", "confirm"):
        _ = await sync.sync_source(source, HostGrant(), db_sources.Due(request, 0))

    assert memory.cursors == ["kept", "kept", None]


async def test_a_refused_folder_lands_the_rest_deletes_nothing_and_names_it(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    """As rclone, a listing the source refused in part mirrors what it lists and deletes nothing missing."""
    memory.files["a.md"] = b"# A"
    memory.refused = ["private"]
    state.mirror("private/b.md", "c.md")

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.deleted, report.refused) == (1, 0, ["private"])
    assert sorted(state.items) == ["a.md", "c.md", "private/b.md"]
    assert state.finished[-1]["cursor"] == "kept"
    assert "refused to list private" in str(state.finished[-1]["error"])


async def test_a_sync_downloads_again_what_the_mirror_lost_from_a_full_listing(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    memory.files.update({"a.md": b"# A", "b.md": b"# B"})
    state.mirror(*memory.files)
    (state.store.workspace_dir() / "b.md").unlink()

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert [e.description_path for e in state.index.indexed] == ["b.md"]
    assert report.downloaded == 1 and memory.cursors == [None]


async def test_a_sync_bisects_a_failed_batch(
    source: catalog.MirroredSource, state: State, memory: Memory
) -> None:
    """One bad file costs only its halves."""
    memory.files.update({f"{n}.md": b"# N" for n in range(4)})
    state.rejected = {"3.md"}

    report = await sync.sync_source(source, HostGrant(), DUE)

    assert (report.downloaded, report.failed) == (3, 1)
    assert [size for size in state.puts if size] == [4, 2, 2, 1, 1]
    assert sorted(state.items) == ["0.md", "1.md", "2.md"]


async def test_a_sync_deletes_a_sidecar_before_it_moves_its_case_base(
    source: catalog.MirroredSource, state: State
) -> None:
    """A sidecar gone from the source leaves first, so the move cannot carry it along."""
    sidecar = Part("cars", "sidecar", ".csv").path
    state.mirror("cars.csv", sidecar)
    moved = (_item("cars.csv"), _item("fleet.csv", id="cars.csv"))

    _ = await sync._apply(
        source,
        state.store,
        sync.SyncPlan(moves=(moved,), deletes=(_item(sidecar),)),
        sync.Progress(),
    )

    assert sorted(path.name for path in state.store.workspace_dir().iterdir()) == [
        "fleet.csv"
    ]


async def test_drift_is_a_missing_file_or_a_missing_or_unindexed_row(
    source: catalog.MirroredSource, index: FakeIndex
) -> None:
    store = HostGrant().workspace(SourceScope(source.id))

    for path in ("a.pdf", "b.pdf", "d.pdf", "cars.csv"):
        store.workspace_dir().joinpath(path).write_text("x")

    known = [_item(path) for path in ("a.pdf", "b.pdf", "c.pdf", "d.pdf", "cars.csv")]

    for stem, digest in {"a": "x", "b": None, "c": "x", "cars": "x"}.items():
        index.add(store, entry(stem), digest=digest)

    assert await sync._drift(store, known) == {"b.pdf", "c.pdf", "d.pdf"}


@pytest.mark.parametrize("source", [["team"]], indirect=True)
def test_a_source_is_seen_by_its_audience_and_written_by_nobody(
    source: catalog.MirroredSource,
) -> None:
    _ = source

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
    source: catalog.MirroredSource, index: FakeIndex
) -> None:
    """An admin writing the global scope can neither write, wipe, nor rework a source's mirror."""
    _ = source, index
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


def test_a_source_names_only_available_pipelines(memory: Memory) -> None:
    config = SourceSettings.model_validate(
        {"connector": "memory", "url": "https://cloud.test/", "chunking": "nope"}
    )

    with pytest.raises(ValueError, match="sources.wiki.chunking"):
        _ = catalog.MirroredSource.build(
            "wiki", config, {"memory": lambda _host: memory}
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
    data_dir: Path, state: State
) -> None:
    """Its mirror is the only copy of what its source lost, so a configuration that dropped it by mistake loses nothing."""
    state.stored = {"gone", "held"}
    state.holding = {"held"}
    mirrors = data_dir / "sources"

    for name in ("gone", "held"):
        (mirrors / name).mkdir(parents=True)

    await sync.prune_sources()
    assert [mirror.name for mirror in mirrors.iterdir()] == ["held"]

    await sync.prune_sources(keep_held=False)
    assert not any(mirrors.iterdir())
