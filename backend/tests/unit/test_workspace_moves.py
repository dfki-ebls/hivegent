"""Unit tests for workspace move/delete semantics through the changeset gateway.

These pin the rules that previously corrupted workspaces: moves are
planned and validated before any rename (no torn entries), an existing
directory destination means move-into (``mv`` semantics), SQL moves are
scoped to exactly the entry (a same-named sibling directory keeps its
rows), explicitly created directories survive emptying out, the items of
one changeset may not overlap, and a failure anywhere restores every file
and commits no row.

The SQL layer is stubbed with a recording fake; the live-DB behaviour of
the repository itself is covered by the dev-stack smoke tests.
"""

import os
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from hivegent import workspace
from hivegent.auth import User
from hivegent.changes import (
    Changeset,
    ChangesetSummary,
    CreateDir,
    Delete,
    FileDiff,
    Move,
    Operation,
    PathMove,
    Write,
)
from hivegent.chunkers.base import DocumentMetadata
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.db import engine as db_engine
from hivegent.entries import (
    ContentStat,
    original_path_for_stem,
    stem_path_from_reference,
)
from hivegent.server.models import ChangesRequest, MoveDestination, WorkspacePath
from hivegent.server.routes import documents as documents_routes
from hivegent.store import Casebase
from hivegent.workspace import Location, changeset
from hivegent.workspace import locks as workspace_locks

_USER = User(id="testuser")


def _move(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> Move[Location]:
    return Move(Location(src_store, src), Location(dst_store, dst))


async def _apply(*operations: Operation[Location]) -> tuple[str, ...]:
    return await workspace.apply_changeset(Changeset(operations))


async def _move_one(
    src_store: Casebase, dst_store: Casebase, src: str, dst: str
) -> tuple[str, ...]:
    return await _apply(_move(src_store, dst_store, src, dst))


async def _delete_one(store: Casebase, path: str) -> tuple[str, ...]:
    return await _apply(Delete(Location(store, path)))


def _doc(stem: str, original_suffix: str | None = None) -> DocumentMetadata:
    original = original_path_for_stem(stem, original_suffix)
    return DocumentMetadata(
        entry_kind="convertible",
        stem_path=stem,
        description_path=f"{stem}.md",
        original_path=original,
        assets_dir=None,
        mime=None,
        origin="upload",
        generated_by="converter",
        files=[f"{stem}.md"] + ([original] if original else []),
        id="doc-id",
        pipeline="recursive",
        created_at=datetime.now(UTC),
        chunks=[],
    )


@dataclass(slots=True)
class FakeRepository:
    """Recording stand-in for :mod:`hivegent.db.documents`."""

    docs: dict[str, DocumentMetadata] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    # Outcomes of every transaction the gateway opened.
    transactions: list[str] = field(default_factory=list)
    # A stem whose row move fails, to exercise the rollback.
    failing_stem: str | None = None

    @property
    def moves(self) -> list[tuple[str, ...]]:
        """Every row move as ``(kind, source, destination, source store, destination store)``.

        A moved row waits on a temporary stem between leaving and landing, so
        the two halves are composed back into the move they make.
        """
        parked: dict[str, tuple[str, str]] = {}
        moves: list[tuple[str, ...]] = []
        for name, src, dst, src_store, dst_store in (c for c in self.calls if len(c) == 5):
            if dst.startswith(".changeset-park/"):
                parked[dst] = (src, src_store)
            else:
                origin, origin_store = parked.pop(src, (src, src_store))
                moves.append((name, origin, dst, origin_store, dst_store))
        return moves

    def moved(self) -> list[tuple[str, str, str]]:
        return sorted((name, src, dst) for name, src, dst, *_stores in self.moves)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[object]:
        try:
            yield object()
        except BaseException:
            self.transactions.append("rolled back")
            raise
        self.transactions.append("committed")

    async def get_entry_metadata(
        self, store: Casebase, reference: str
    ) -> DocumentMetadata | None:
        return self.docs.get(stem_path_from_reference(reference))

    async def get_entries_metadata(
        self, references: Iterable[tuple[Casebase, str]]
    ) -> dict[tuple[Casebase, str], DocumentMetadata]:
        stems = {(store, stem_path_from_reference(ref)) for store, ref in references}
        return {(store, stem): self.docs[stem] for store, stem in stems if stem in self.docs}

    async def move_document(
        self,
        src_store: Casebase,
        src: str,
        dst_store: Casebase,
        dst: str,
        *,
        s: object = None,
    ) -> bool:
        assert s is not None
        if src == self.failing_stem:
            raise RuntimeError("simulated row failure")
        self.calls.append(("move_document", src, dst, src_store.store_key, dst_store.store_key))
        return True

    async def move_subtree(
        self,
        src_store: Casebase,
        src: str,
        dst_store: Casebase,
        dst: str,
        *,
        s: object = None,
    ) -> None:
        assert s is not None
        self.calls.append(("move_subtree", src, dst, src_store.store_key, dst_store.store_key))

    async def delete_subtree(
        self, store: Casebase, prefix: str, *, s: object = None
    ) -> int:
        self.calls.append(("delete_subtree", prefix))
        return 0

    async def delete_document(
        self, store: Casebase, reference: str, *, s: object = None
    ) -> bool:
        self.calls.append(("delete_document", reference))
        return True


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepository:
    fake = FakeRepository()
    monkeypatch.setattr(db_engine, "session", fake.session)
    for name in (
        "get_entry_metadata",
        "get_entries_metadata",
        "move_document",
        "move_subtree",
        "delete_subtree",
        "delete_document",
    ):
        monkeypatch.setattr(db_documents, name, getattr(fake, name))
    return fake


@pytest.fixture()
def workspace_dir(user_store: Casebase) -> Path:
    path = user_store.workspace_dir(settings.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def layout(workspace_dir: Path, repo: FakeRepository) -> Path:
    for stem in ("a", "b", "dir/c"):
        (workspace_dir / f"{stem}.md").parent.mkdir(parents=True, exist_ok=True)
        (workspace_dir / f"{stem}.md").write_text(stem)
        repo.docs[stem] = _doc(stem)
    return workspace_dir


class TestMoveDocument:
    async def test_moves_entry_with_original_and_leaves_sibling_directory(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """Description and original move together while a same-named sibling
        directory keeps its files and rows: exactly one ``move_document``, never
        a subtree sweep that would hijack the directory's rows."""
        (workspace_dir / "notes.md").write_text("desc")
        (workspace_dir / "notes.pdf").write_bytes(b"%PDF")
        (workspace_dir / "notes").mkdir()
        (workspace_dir / "notes/inner.md").write_text("inner")
        repo.docs["notes"] = _doc("notes", original_suffix=".pdf")

        await _move_one(
            user_store, user_store, "notes.md", "archive/notes.md"
        )

        assert (workspace_dir / "archive/notes.md").is_file()
        assert (workspace_dir / "archive/notes.pdf").is_file()
        assert not (workspace_dir / "notes.md").exists()
        assert not (workspace_dir / "notes.pdf").exists()
        assert (workspace_dir / "notes/inner.md").is_file()
        assert repo.moved() == [("move_document", "notes", "archive/notes")]

    async def test_existing_directory_destination_moves_into_it(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "report.md").write_text("r")
        (workspace_dir / "report.pdf").write_bytes(b"%PDF")
        (workspace_dir / "archive").mkdir()
        repo.docs["report"] = _doc("report", original_suffix=".pdf")

        await _move_one(user_store, user_store, "report.md", "archive")

        assert (workspace_dir / "archive/report.md").is_file()
        assert (workspace_dir / "archive/report.pdf").is_file()

    async def test_conflict_is_detected_before_any_rename(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """A blocked original target must not leave a half-moved (duplicated) entry."""
        (workspace_dir / "data").write_text("bin")
        (workspace_dir / "data.md").write_text("d")
        (workspace_dir / "blocked/data").mkdir(parents=True)
        repo.docs["data"] = _doc("data", original_suffix="")

        with pytest.raises(HTTPException) as exc:
            await _move_one(
                user_store, user_store, "data.md", "blocked/data.md"
            )

        assert exc.value.status_code == 409
        assert (workspace_dir / "data.md").is_file()
        assert (workspace_dir / "data").is_file()
        assert repo.calls == []

    async def test_dotted_stem_moves_intact(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """A stem containing dots (``a.tar``) must reach SQL verbatim, not
        re-stemmed down to ``a``."""
        (workspace_dir / "a.tar.md").write_text("d")
        (workspace_dir / "a.tar.gz").write_bytes(b"x")
        repo.docs["a.tar"] = _doc("a.tar", original_suffix=".gz")

        await _move_one(
            user_store, user_store, "a.tar.md", "dir/a.tar.md"
        )

        assert (workspace_dir / "dir/a.tar.md").is_file()
        assert (workspace_dir / "dir/a.tar.gz").is_file()
        assert repo.moved() == [("move_document", "a.tar", "dir/a.tar")]

    async def test_rejects_destination_reserved_by_upload(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        repo: FakeRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(workspace_locks, "_states", {})
        (workspace_dir / "report.md").write_text("r")
        repo.docs["report"] = _doc("report")
        workspace_locks._add_inflight(user_store, "archive/report.md")

        with pytest.raises(HTTPException) as exc:
            await _move_one(
                user_store, user_store, "report.md", "archive/report.md"
            )

        assert exc.value.status_code == 409
        assert (workspace_dir / "report.md").is_file()
        assert repo.calls == []


class TestMoveDirectory:
    async def test_existing_directory_destination_moves_into_it(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "images").mkdir()
        (workspace_dir / "images/x.md").write_text("x")
        (workspace_dir / "archive").mkdir()

        await _move_one(user_store, user_store, "images", "archive")

        assert (workspace_dir / "archive/images/x.md").is_file()
        assert repo.moved() == [("move_subtree", "images", "archive/images")]

    async def test_move_into_own_subtree_is_rejected(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "images").mkdir()

        with pytest.raises(HTTPException) as exc:
            await _move_one(
                user_store, user_store, "images", "images/sub"
            )

        assert exc.value.status_code == 400
        assert (workspace_dir / "images").is_dir()

    async def test_rejects_destination_containing_reserved_upload(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        repo: FakeRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(workspace_locks, "_states", {})
        (workspace_dir / "images").mkdir()
        workspace_locks._add_inflight(user_store, "archive/images/pending.md")

        with pytest.raises(HTTPException) as exc:
            await _move_one(
                user_store, user_store, "images", "archive/images"
            )

        assert exc.value.status_code == 409
        assert (workspace_dir / "images").is_dir()
        assert repo.calls == []


class TestCrossStoreMove:
    """Migrating between workspaces (personal ↔ group): the files relocate to
    the destination store's workspace tree and the SQL owner flips with them."""

    @pytest.fixture()
    def group_store(self, data_dir: Path) -> Casebase:
        _ = data_dir
        return Casebase(kind="group", id="team")

    async def test_document_relocates_to_group_workspace(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        group_store: Casebase,
        repo: FakeRepository,
    ) -> None:
        """A same-named cross-store move re-homes the entry: its files land in
        the group tree, leave the personal one, and the owner flip is requested
        for both the description row and its assets subtree."""
        group_dir = group_store.workspace_dir(settings.data_dir)
        (workspace_dir / "report.md").write_text("[a](report.assets/a.png)")
        (workspace_dir / "report.pdf").write_bytes(b"%PDF")
        (workspace_dir / "report.assets").mkdir()
        (workspace_dir / "report.assets/a.png").write_bytes(b"img")
        doc = _doc("report", original_suffix=".pdf")
        repo.docs["report"] = doc.model_copy(update={"assets_dir": "report.assets"})

        await _move_one(user_store, group_store, "report.md", "report.md")

        assert (group_dir / "report.md").is_file()
        assert (group_dir / "report.pdf").is_file()
        assert (group_dir / "report.assets/a.png").is_file()
        assert not (workspace_dir / "report.md").exists()
        assert not (workspace_dir / "report.pdf").exists()
        # Same basename → the asset references are untouched.
        assert (group_dir / "report.md").read_text() == "[a](report.assets/a.png)"
        assert [(name, *stores) for name, _src, _dst, *stores in repo.moves] == [
            ("move_document", "user:testuser", "group:team"),
            ("move_subtree", "user:testuser", "group:team"),
        ]

    async def test_document_blocked_by_existing_destination(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        group_store: Casebase,
        repo: FakeRepository,
    ) -> None:
        """A destination already occupied in the group is a 409, and nothing
        leaves the source workspace."""
        group_dir = group_store.workspace_dir(settings.data_dir)
        (workspace_dir / "notes.md").write_text("mine")
        (group_dir / "notes.md").write_text("theirs")
        repo.docs["notes"] = _doc("notes")

        with pytest.raises(HTTPException) as exc:
            await _move_one(
                user_store, group_store, "notes.md", "notes.md"
            )

        assert exc.value.status_code == 409
        assert (workspace_dir / "notes.md").read_text() == "mine"
        assert repo.calls == []

    async def test_directory_relocates_to_group_workspace(
        self,
        user_store: Casebase,
        workspace_dir: Path,
        group_store: Casebase,
        repo: FakeRepository,
    ) -> None:
        group_dir = group_store.workspace_dir(settings.data_dir)
        (workspace_dir / "shared").mkdir()
        (workspace_dir / "shared/x.md").write_text("x")

        await _move_one(user_store, group_store, "shared", "shared")

        assert (group_dir / "shared/x.md").is_file()
        assert not (workspace_dir / "shared").exists()
        assert [(name, *stores) for name, _src, _dst, *stores in repo.moves] == [
            ("move_subtree", "user:testuser", "group:team")
        ]


class TestNativeSemanticGuards:
    """Operations that previously crashed with an ``OSError`` (a 500 to the
    client) or silently corrupted the workspace now fail with clear 4xx."""

    async def test_delete_directory_rejects_empty_path(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """A bare scope root must not wipe the workspace while rows survive."""
        (workspace_dir / "docs").mkdir()
        (workspace_dir / "docs/a.md").write_text("a")

        with pytest.raises(HTTPException) as exc:
            await _delete_one(user_store, "")

        assert exc.value.status_code == 400
        assert (workspace_dir / "docs/a.md").is_file()

    async def test_delete_refuses_the_kind_it_does_not_expect(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "a.md").write_text("a")

        with pytest.raises(HTTPException, match="Directory not found"):
            await _apply(Delete(Location(user_store, "a.md"), expect="dir"))

        assert (workspace_dir / "a.md").is_file()

    async def test_upload_below_file_blocked_parent_is_409(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """A destination whose parent component is a file is rejected up front
        instead of surfacing as an ``OSError`` 500."""
        (workspace_dir / "afile").write_text("x")
        with pytest.raises(HTTPException) as exc:
            await workspace.upload(user_store, "afile/doc.md", b"hello")
        assert exc.value.status_code == 409

    async def test_directory_api_refuses_assets_paths(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """Hidden ``.assets`` storage cannot be created or renamed directly."""
        (workspace_dir / "report.assets").mkdir()

        with pytest.raises(HTTPException) as create_exc:
            await _apply(CreateDir(Location(user_store, "x.assets")))
        with pytest.raises(HTTPException) as move_exc:
            await _move_one(
                user_store, user_store, "report.assets", "elsewhere"
            )

        assert create_exc.value.status_code == 400
        assert move_exc.value.status_code == 400


class TestDeleteKeepsDirectories:
    async def test_deleting_last_file_keeps_parent_directory(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "keep").mkdir()
        (workspace_dir / "keep/doc.md").write_text("body")
        repo.docs["keep/doc"] = _doc("keep/doc")

        await _delete_one(user_store, "keep/doc.md")

        assert (workspace_dir / "keep").is_dir()
        assert not (workspace_dir / "keep/doc.md").exists()

    async def test_deletes_stray_original_without_row_or_description(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """Hand-dropped binaries survive reconciliation, so the API must be
        able to remove them despite having no SQL row and no description."""
        (workspace_dir / "loose.bin").write_bytes(b"\x00")

        await _delete_one(user_store, "loose.bin")

        assert not (workspace_dir / "loose.bin").exists()

    async def test_deletes_sibling_original_the_row_has_not_recorded(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """The row is an index over the filesystem, so an original it does not
        name yet is still part of the entry the inventory groups and shows."""
        (workspace_dir / "notes.md").write_text("body")
        (workspace_dir / "notes.pdf").write_bytes(b"%PDF")
        repo.docs["notes"] = _doc("notes")

        await _delete_one(user_store, "notes.md")

        assert not (workspace_dir / "notes.md").exists()
        assert not (workspace_dir / "notes.pdf").exists()


class TestChangesets:
    """Batches apply at once, all or nothing: sources as they are now, destinations as they end up."""

    @pytest.mark.parametrize(
        ("pairs", "deletes", "message"),
        [
            ([("a.md", "x.md"), ("a.md", "y.md")], [], "more than one change"),
            ([("a.md", "x.md"), ("b.md", "x.md")], [], "more than one change"),
            ([("a.md", "x.md")], ["a.md"], "more than one change"),
            ([("a.md", "b.md")], [], "already exists"),
            ([("dir", "moved"), ("a.md", "moved/c.md")], [], "already exists"),
        ],
    )
    async def test_conflicting_items_are_refused_before_anything_changes(
        self,
        user_store: Casebase,
        layout: Path,
        repo: FakeRepository,
        pairs: list[tuple[str, str]],
        deletes: list[str],
        message: str,
    ) -> None:
        operations = (
            *(_move(user_store, user_store, src, dst) for src, dst in pairs),
            *(Delete(Location(user_store, path)) for path in deletes),
        )

        with pytest.raises(HTTPException, match=message):
            await workspace.plan_changeset(Changeset(operations))

        assert sorted(p.name for p in layout.iterdir()) == ["a.md", "b.md", "dir"]
        assert repo.calls == []

    async def test_written_files_and_new_originals_are_checked_in_the_final_state(
        self, user_store: Casebase, layout: Path
    ) -> None:
        async def plan(*operations: Operation[Location]) -> workspace.PlannedChangeset:
            return await workspace.plan_changeset(Changeset(operations))

        # A file one item writes cannot be the directory another item fills.
        with pytest.raises(HTTPException, match="'~/x' is a file"):
            await plan(
                Write(Location(user_store, "x"), "1"),
                Write(Location(user_store, "x/y.md"), "2"),
            )

        # A new original's stem is taken by an entry a move lands on it.
        with pytest.raises(HTTPException, match="already exists"):
            await plan(
                _move(user_store, user_store, "a.md", "x.md"),
                Write(Location(user_store, "x.txt"), "x"),
            )

        # And freed by a delete of the entry holding it now.
        with pytest.raises(HTTPException, match="already exists"):
            await plan(Write(Location(user_store, "b.txt"), "x"))
        _ = await plan(
            Delete(Location(user_store, "b.md")), Write(Location(user_store, "b.txt"), "x")
        )

    @pytest.mark.parametrize(
        ("pairs", "deletes", "expected"),
        [
            # A swap and a rotation, which no order of single moves can make.
            ([("a.md", "b.md"), ("b.md", "a.md")], [], {"a.md": "b", "b.md": "a", "dir/c.md": "dir/c"}),
            (
                [("a.md", "b.md"), ("b.md", "dir/c.md"), ("dir/c.md", "a.md")],
                [],
                {"a.md": "dir/c", "b.md": "a", "dir/c.md": "b"},
            ),
            # A chain, and a move onto a path the same call deletes.
            ([("a.md", "b.md"), ("b.md", "x.md")], [], {"b.md": "a", "x.md": "b", "dir/c.md": "dir/c"}),
            ([("a.md", "b.md")], ["b.md"], {"b.md": "a", "dir/c.md": "dir/c"}),
            # Moves and deletes inside a directory another move takes.
            ([("dir", "moved"), ("dir/c.md", "c.md")], [], {"a.md": "a", "b.md": "b", "c.md": "dir/c"}),
            ([("dir", "moved")], ["dir/c.md"], {"a.md": "a", "b.md": "b"}),
            (
                [("a.md", "dir/a.md"), ("dir", "moved")],
                [],
                {"b.md": "b", "dir/a.md": "a", "moved/c.md": "dir/c"},
            ),
        ],
    )
    async def test_items_apply_at_once(
        self,
        user_store: Casebase,
        layout: Path,
        repo: FakeRepository,
        pairs: list[tuple[str, str]],
        deletes: list[str],
        expected: dict[str, str],
    ) -> None:
        await _apply(
            *(_move(user_store, user_store, src, dst) for src, dst in pairs),
            *(Delete(Location(user_store, path)) for path in deletes),
        )

        files = {
            str(path.relative_to(layout)): path.read_text()
            for path in layout.rglob("*.md")
        }
        assert files == expected
        assert repo.transactions == ["committed"]

    async def test_a_swap_keeps_every_row(
        self, user_store: Casebase, layout: Path, repo: FakeRepository
    ) -> None:
        await _apply(
            _move(user_store, user_store, "a.md", "b.md"),
            _move(user_store, user_store, "b.md", "a.md"),
        )

        assert repo.moved() == [("move_document", "a", "b"), ("move_document", "b", "a")]

    async def test_a_case_only_rename_renames(
        self, user_store: Casebase, layout: Path, repo: FakeRepository
    ) -> None:
        await _move_one(user_store, user_store, "a.md", "A.md")

        assert "A.md" in {path.name for path in layout.iterdir()}
        assert "a.md" not in {path.name for path in layout.iterdir()}
        assert repo.moved() == [("move_document", "a", "A")]

    async def test_a_case_insensitive_filesystem_sees_one_destination(
        self,
        user_store: Casebase,
        layout: Path,
        repo: FakeRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(changeset, "folds_case", lambda _directory: True)

        with pytest.raises(HTTPException, match="more than one change"):
            await workspace.plan_changeset(
                Changeset(
                    (
                        _move(user_store, user_store, "a.md", "X.md"),
                        _move(user_store, user_store, "b.md", "x.md"),
                    )
                )
            )

    async def test_an_empty_directory_is_created_once(
        self, user_store: Casebase, layout: Path, repo: FakeRepository
    ) -> None:
        await _apply(CreateDir(Location(user_store, "new/deep")))

        assert (layout / "new/deep").is_dir()
        with pytest.raises(HTTPException) as exc:
            await _apply(CreateDir(Location(user_store, "a.md")))
        assert exc.value.status_code == 409

    async def test_a_replaced_file_fails_its_basis_despite_its_stat(
        self, user_store: Casebase, layout: Path, repo: FakeRepository
    ) -> None:
        """A same-size replacement with the old mtime is a new inode."""
        live = layout / "a.md"
        seen = ContentStat.from_path(live)
        assert seen is not None
        (layout / "other").write_text("z")
        os.utime(layout / "other", ns=(seen.mtime_ns, seen.mtime_ns))
        (layout / "other").replace(live)

        with pytest.raises(HTTPException) as exc:
            await _apply(Delete(Location(user_store, "a.md"), seen))

        assert exc.value.status_code == 409

    async def test_plan_resolves_move_into_directory(
        self, user_store: Casebase, layout: Path
    ) -> None:
        planned = await workspace.plan_changeset(
            Changeset((_move(user_store, user_store, "a.md", "dir"),))
        )

        assert planned.changeset.operations == (_move(user_store, user_store, "a.md", "dir/a.md"),)

    async def test_plan_summarizes_what_a_person_approves(
        self, user_store: Casebase, layout: Path
    ) -> None:
        (layout / "b.pdf").write_bytes(b"%PDF")

        planned = await workspace.plan_changeset(
            Changeset(
                (
                    _move(user_store, user_store, "a.md", "b.md"),
                    Delete(Location(user_store, "b.md")),
                    _move(user_store, user_store, "dir", "moved"),
                    Write(Location(user_store, "moved/c.md"), "C"),
                    Write(Location(user_store, "new.md"), "x\n"),
                    CreateDir(Location(user_store, "empty")),
                )
            )
        )

        assert planned.summary == ChangesetSummary(
            creates=(FileDiff("~/new.md", "--- /dev/null\n+++ ~/new.md\n@@ -0,0 +1 @@\n+x\n"),),
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

    async def test_a_failed_row_change_restores_every_file(
        self, user_store: Casebase, layout: Path, repo: FakeRepository
    ) -> None:
        repo.failing_stem = "b"

        with pytest.raises(RuntimeError, match="simulated row failure"):
            await _apply(
                _move(user_store, user_store, "a.md", "archive/a.md"),
                _move(user_store, user_store, "b.md", "archive/b.md"),
                Delete(Location(user_store, "dir")),
            )

        assert repo.transactions == ["rolled back"]
        assert (layout / "a.md").read_text() == "a"
        assert (layout / "b.md").read_text() == "b"
        assert (layout / "dir/c.md").read_text() == "dir/c"
        assert not (layout / "archive").exists()

    async def test_a_write_follows_the_move_that_carries_its_file(
        self,
        user_store: Casebase,
        layout: Path,
        repo: FakeRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """What a sandbox diff produces for a document renamed and then edited."""

        async def _index(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(changeset, "chunk_and_index_document", _index)

        await _apply(
            Write(Location(user_store, "archive/a.md"), "edited"),
            _move(user_store, user_store, "a.md", "archive/a.md"),
            _move(user_store, user_store, "dir", "moved"),
            Write(Location(user_store, "moved/c.md"), "also edited"),
        )

        assert (layout / "archive/a.md").read_text() == "edited"
        assert (layout / "moved/c.md").read_text() == "also edited"
        assert not (layout / "a.md").exists()
        assert repo.moved() == [
            ("move_document", "a", "archive/a"),
            ("move_subtree", "dir", "moved"),
        ]


class TestChangesRoute:
    """``POST /changes`` sends every operation of a request through one changeset."""

    async def test_mixed_batch_applies_at_once(
        self, layout: Path, repo: FakeRepository
    ) -> None:
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

        assert (layout / "x.md").read_text() == "a"
        assert (layout / "moved/c.md").read_text() == "dir/c"
        assert not (layout / "b.md").exists()
        assert (layout / "new").is_dir()
        assert repo.moved() == [
            ("move_document", "a", "x"),
            ("move_subtree", "dir", "moved"),
        ]
        assert ("delete_document", "b.md") in repo.calls
        assert repo.transactions == ["committed"]

    async def test_deleting_a_folder_as_an_entry_changes_nothing(
        self, layout: Path, repo: FakeRepository
    ) -> None:
        request = ChangesRequest.model_validate(
            {
                "operations": [
                    {"kind": "move", "source": "~/a.md", "destination": "~/x.md"},
                    {"kind": "delete", "path": "~/dir", "expect": "entry"},
                ]
            }
        )

        with pytest.raises(HTTPException) as exc:
            await documents_routes.apply_changes(request, _USER)

        assert exc.value.status_code == 404
        assert "~/dir" in str(exc.value.detail)
        assert (layout / "a.md").is_file()
        assert (layout / "dir/c.md").is_file()
        assert repo.calls == []

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
        self, layout: Path, repo: FakeRepository
    ) -> None:
        """Each shortcut is one operation of ``POST /changes``, kind checks included."""
        await documents_routes.move_document("~/a.md", MoveDestination(destination="~/x.md"), _USER)

        with pytest.raises(HTTPException) as exc:
            await documents_routes.delete_document("~/dir", _USER)

        assert exc.value.status_code == 404
        await documents_routes.delete_directory(WorkspacePath(path="~/dir"), _USER)

        assert (layout / "x.md").read_text() == "a"
        assert not (layout / "dir").exists()
        assert repo.moved() == [("move_document", "a", "x")]
