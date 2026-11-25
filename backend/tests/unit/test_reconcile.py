"""Unit tests pinning the filesystem-as-source-of-truth reconcile policy.

The filesystem is authoritative for content: reconciliation ingests on-disk
markdown into SQL and drops rows whose description vanished, but it must never
delete or rename workspace files, and a file without a description stays inert
content rather than an orphan to prune.
"""

from pathlib import Path

import pytest

from hivegent import reconcile, workspace
from hivegent.db import documents as db_documents
from hivegent.db.documents import EntryState
from hivegent.metadata import EntryMetadata
from hivegent.store import Workspace


def _state(stem: str) -> EntryState:
    """A row whose content was never indexed, so its description is."""
    metadata = EntryMetadata(
        entry_kind="user_markdown",
        stem_path=stem,
        description_path=f"{stem}.md",
        original_path=None,
        assets_dir=None,
        origin="upload",
        generated_by="user",
    )

    return EntryState(
        content_digest=None, chunker=None, content_stat=None, metadata=metadata
    )


@pytest.fixture()
def workspace_dir(user_store: Workspace, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace root with the SQL layer stubbed for reconciliation runs."""

    async def get_entry_state(store: Workspace, reference: str) -> None:
        return None

    indexed: list[str] = []

    async def chunk_and_index(
        store: Workspace, filename: str, *args: object, **kwargs: object
    ) -> None:
        indexed.append(filename)

    async def delete_chunked_document(store: Workspace, reference: str) -> bool:
        return False

    monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", chunk_and_index)
    monkeypatch.setattr(
        workspace.indexing, "delete_chunked_document", delete_chunked_document
    )
    path = user_store.workspace_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


async def test_reconcile_ingests_markdown_and_keeps_stray_files(
    user_store: Workspace, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace_dir / "doc.md").write_text("body")
    (workspace_dir / "loose.bin").write_bytes(b"\x00")
    (workspace_dir / "gone.assets").mkdir()
    (workspace_dir / "gone.assets/fig.png").write_bytes(b"\x89PNG")

    async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
        return {"doc.md": _state("doc")}

    monkeypatch.setattr(reconcile.db_documents, "get_entry_states", get_entry_states)

    synced = await reconcile.reconcile_store(user_store)

    assert synced == 1
    # Files the SQL index does not vouch for stay untouched: disk is truth.
    assert (workspace_dir / "loose.bin").exists()
    assert (workspace_dir / "gone.assets/fig.png").exists()


async def test_reconcile_leaves_files_without_a_description_inert(
    user_store: Workspace, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file dropped by hand is no entry until it is uploaded, and keeps its name."""
    (workspace_dir / "settings.ini").write_text("host = local\n")
    (workspace_dir / "notes.md").write_text("ok")

    async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
        return {}

    monkeypatch.setattr(reconcile.db_documents, "get_entry_states", get_entry_states)

    synced = await reconcile.reconcile_store(user_store)

    assert synced == 1
    assert sorted(path.name for path in workspace_dir.iterdir()) == [
        "notes.md",
        "settings.ini",
    ]


async def test_reconcile_skips_a_failing_entry_and_ingests_the_rest(
    user_store: Workspace, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace_dir / "good.md").write_text("ok")
    (workspace_dir / "bad.md").write_text("boom")

    async def chunk_and_index(
        store: Workspace, filename: str, *args: object, **kwargs: object
    ) -> None:
        if filename == "bad.md":
            raise RuntimeError("chunker exploded")

    async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
        return {}

    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", chunk_and_index)
    monkeypatch.setattr(reconcile.db_documents, "get_entry_states", get_entry_states)

    # One poison file must not abort the batch: the healthy entry still ingests.
    synced = await reconcile.reconcile_store(user_store)

    assert synced == 1


async def test_reconcile_drops_rows_whose_description_vanished(
    user_store: Workspace, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
        return {"ghost.md": _state("ghost")}

    dropped: list[str] = []

    async def delete_chunked_document(store: Workspace, reference: str) -> bool:
        dropped.append(reference)
        return True

    monkeypatch.setattr(reconcile.db_documents, "get_entry_states", get_entry_states)
    monkeypatch.setattr(
        workspace.indexing, "delete_chunked_document", delete_chunked_document
    )

    synced = await reconcile.reconcile_store(user_store)

    assert dropped == ["ghost.md"]
    assert synced == 1


@pytest.mark.parametrize("unreachable", ["root", "folder"])
async def test_reconcile_keeps_every_row_of_a_store_it_cannot_list(
    user_store: Workspace,
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    unreachable: str,
) -> None:
    """An unmounted or unreadable store fails loudly rather than reading as empty."""
    deleted: list[str] = []

    async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
        return {"doc.md": _state("doc")}

    async def delete_chunked_document(store: Workspace, reference: str) -> bool:
        deleted.append(reference)
        return True

    monkeypatch.setattr(reconcile.db_documents, "get_entry_states", get_entry_states)
    monkeypatch.setattr(
        workspace.indexing, "delete_chunked_document", delete_chunked_document
    )

    if unreachable == "root":
        workspace_dir.rmdir()
    else:
        (workspace_dir / "locked").mkdir(mode=0)

    try:
        with pytest.raises(OSError):
            _ = await reconcile.reconcile_store(user_store)
    finally:
        if unreachable == "folder":
            (workspace_dir / "locked").chmod(0o700)

    assert deleted == []
