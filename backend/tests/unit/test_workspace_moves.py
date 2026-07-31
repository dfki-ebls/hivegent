"""Unit tests for workspace move/delete filesystem semantics.

These pin the rules that previously corrupted workspaces: moves are
planned and validated before any rename (no torn entries), an existing
directory destination means move-into (``mv`` semantics), SQL moves are
scoped to exactly the entry (a same-named sibling directory keeps its
rows), and explicitly created directories survive emptying out.

The SQL layer is stubbed with a recording fake; the live-DB behaviour of
the repository itself is covered by the dev-stack smoke tests.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from hivegent import workspace
from hivegent.chunkers.base import DocumentMetadata
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.entries import original_path_for_stem, stem_path_from_reference
from hivegent.store import Casebase
from hivegent.workspace import commit


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
    # (name, src_store_key, dst_store_key) for each SQL move, so a cross-store
    # test can assert the owner flip is requested against the right casebases.
    store_moves: list[tuple[str, str, str]] = field(default_factory=list)

    async def get_document(
        self, store: Casebase, reference: str
    ) -> DocumentMetadata | None:
        return self.docs.get(stem_path_from_reference(reference))

    async def move_document(
        self, src_store: Casebase, src: str, dst_store: Casebase, dst: str
    ) -> bool:
        self.calls.append(("move_document", src, dst))
        self.store_moves.append(
            ("move_document", src_store.store_key, dst_store.store_key)
        )
        return True

    async def move_subtree(
        self, src_store: Casebase, src: str, dst_store: Casebase, dst: str
    ) -> None:
        self.calls.append(("move_subtree", src, dst))
        self.store_moves.append(
            ("move_subtree", src_store.store_key, dst_store.store_key)
        )

    async def delete_subtree(self, store: Casebase, prefix: str) -> int:
        self.calls.append(("delete_subtree", prefix))
        return 0

    async def delete_document(self, store: Casebase, reference: str) -> bool:
        self.calls.append(("delete_document", reference))
        return True


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepository:
    fake = FakeRepository()
    for name in (
        "get_document",
        "move_document",
        "move_subtree",
        "delete_subtree",
    ):
        monkeypatch.setattr(db_documents, name, getattr(fake, name))

    async def _delete_chunked(store: Casebase, reference: str) -> bool:
        return await fake.delete_document(store, reference)

    monkeypatch.setattr(commit, "delete_chunked_document", _delete_chunked)
    return fake


@pytest.fixture()
def workspace_dir(user_store: Casebase) -> Path:
    path = user_store.workspace_dir(settings.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


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

        resp = await workspace.move_document(
            user_store, user_store, "notes.md", "archive/notes.md"
        )

        assert resp.destination == "archive/notes.md"
        assert (workspace_dir / "archive/notes.md").is_file()
        assert (workspace_dir / "archive/notes.pdf").is_file()
        assert not (workspace_dir / "notes.md").exists()
        assert not (workspace_dir / "notes.pdf").exists()
        assert (workspace_dir / "notes/inner.md").is_file()
        assert repo.calls == [("move_document", "notes", "archive/notes")]

    async def test_existing_directory_destination_moves_into_it(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "report.md").write_text("r")
        (workspace_dir / "report.pdf").write_bytes(b"%PDF")
        (workspace_dir / "archive").mkdir()
        repo.docs["report"] = _doc("report", original_suffix=".pdf")

        resp = await workspace.move_document(
            user_store, user_store, "report.md", "archive"
        )

        assert resp.destination == "archive/report.md"
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
            await workspace.move_document(
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

        resp = await workspace.move_document(
            user_store, user_store, "a.tar.md", "dir/a.tar.md"
        )

        assert resp.destination == "dir/a.tar.md"
        assert (workspace_dir / "dir/a.tar.gz").is_file()
        assert repo.calls == [("move_document", "a.tar", "dir/a.tar")]


class TestMoveDirectory:
    async def test_existing_directory_destination_moves_into_it(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "images").mkdir()
        (workspace_dir / "images/x.md").write_text("x")
        (workspace_dir / "archive").mkdir()

        resp = await workspace.move_directory(
            user_store, user_store, "images", "archive"
        )

        assert resp.destination == "archive/images"
        assert (workspace_dir / "archive/images/x.md").is_file()
        assert repo.calls == [("move_subtree", "images", "archive/images")]

    async def test_move_into_own_subtree_is_rejected(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        (workspace_dir / "images").mkdir()

        with pytest.raises(HTTPException) as exc:
            await workspace.move_directory(
                user_store, user_store, "images", "images/sub"
            )

        assert exc.value.status_code == 400
        assert (workspace_dir / "images").is_dir()


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

        resp = await workspace.move_document(
            user_store, group_store, "report.md", "report.md"
        )

        assert resp.destination == "report.md"
        assert (group_dir / "report.md").is_file()
        assert (group_dir / "report.pdf").is_file()
        assert (group_dir / "report.assets/a.png").is_file()
        assert not (workspace_dir / "report.md").exists()
        assert not (workspace_dir / "report.pdf").exists()
        # Same basename → the asset references are untouched.
        assert (group_dir / "report.md").read_text() == "[a](report.assets/a.png)"
        assert repo.store_moves == [
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
            await workspace.move_document(
                user_store, group_store, "notes.md", "notes.md"
            )

        assert exc.value.status_code == 409
        assert (workspace_dir / "notes.md").read_text() == "mine"
        assert repo.store_moves == []

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

        resp = await workspace.move_directory(
            user_store, group_store, "shared", "shared"
        )

        assert resp.destination == "shared"
        assert resp.files_moved == 1
        assert (group_dir / "shared/x.md").is_file()
        assert not (workspace_dir / "shared").exists()
        assert repo.store_moves == [("move_subtree", "user:testuser", "group:team")]


class TestPruneEmptyDirs:
    async def test_removes_emptied_chain_but_keeps_occupied_dirs(
        self, user_store: Casebase, workspace_dir: Path
    ) -> None:
        """After a bulk move, the emptied source chain vanishes while any
        directory still holding content — even content the tree cannot
        see — survives the non-recursive prune."""
        (workspace_dir / "a/b").mkdir(parents=True)
        (workspace_dir / "c").mkdir()
        (workspace_dir / "c/keep.md").write_text("k")

        await workspace.prune_empty_dirs(user_store, ["a/b/x.md", "c/y.md"])

        assert not (workspace_dir / "a").exists()
        assert (workspace_dir / "c/keep.md").is_file()


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
            await workspace.delete_directory(user_store, "")

        assert exc.value.status_code == 400
        assert (workspace_dir / "docs/a.md").is_file()

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
            await workspace.create_directory(user_store, "x.assets")
        with pytest.raises(HTTPException) as move_exc:
            await workspace.move_directory(
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

        await workspace.delete_document(user_store, "keep/doc.md")

        assert (workspace_dir / "keep").is_dir()
        assert not (workspace_dir / "keep/doc.md").exists()

    async def test_deletes_stray_original_without_row_or_description(
        self, user_store: Casebase, workspace_dir: Path, repo: FakeRepository
    ) -> None:
        """Hand-dropped binaries survive reconciliation, so the API must be
        able to remove them despite having no SQL row and no description."""
        (workspace_dir / "loose.bin").write_bytes(b"\x00")

        await workspace.delete_document(user_store, "loose.bin")

        assert not (workspace_dir / "loose.bin").exists()
