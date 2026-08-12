"""Unit tests pinning the filesystem-as-source-of-truth reconcile policy.

The filesystem is authoritative for content: reconciliation ingests on-disk
markdown into SQL, derives the missing description of a hand-dropped text
file, and drops rows whose description vanished — but it must never delete
workspace files, and a file it cannot project stays inert content rather than
an orphan to prune.
"""

from pathlib import Path

import pytest

from hivegent import reconcile, workspace
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.store import Casebase


@pytest.fixture()
def workspace_dir(user_store: Casebase, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace root with the SQL layer stubbed for reconciliation runs."""

    async def get_entry_state(store: Casebase, reference: str) -> None:
        return None

    indexed: list[str] = []

    async def chunk_and_index(
        store: Casebase, filename: str, *args: object, **kwargs: object
    ) -> None:
        indexed.append(filename)

    async def delete_chunked_document(store: Casebase, reference: str) -> bool:
        return False

    monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", chunk_and_index)
    monkeypatch.setattr(
        workspace.indexing, "delete_chunked_document", delete_chunked_document
    )
    path = user_store.workspace_dir(settings.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def test_reconcile_ingests_markdown_and_keeps_stray_files(
    user_store: Casebase, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace_dir / "doc.md").write_text("body")
    (workspace_dir / "loose.bin").write_bytes(b"\x00")
    (workspace_dir / "gone.assets").mkdir()
    (workspace_dir / "gone.assets/fig.png").write_bytes(b"\x89PNG")

    async def list_document_paths(store: Casebase) -> dict[str, int]:
        return {"doc.md": 1}

    monkeypatch.setattr(
        reconcile.db_documents, "list_document_paths", list_document_paths
    )

    report = await reconcile.reconcile_store(user_store)

    assert report.entries_ingested == 1
    # Files the SQL index does not vouch for stay untouched: disk is truth.
    assert (workspace_dir / "loose.bin").exists()
    assert (workspace_dir / "gone.assets/fig.png").exists()


async def test_reconcile_derives_descriptions_for_hand_dropped_text_files(
    user_store: Casebase, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped config file is picked up; one needing a converter is not."""
    (workspace_dir / "settings.ini").write_text("host = local\n")
    (workspace_dir / "table.csv").write_text("a,b\n1,2\n")
    (workspace_dir / "loose.bin").write_bytes(b"\x00")

    async def list_document_paths(store: Casebase) -> dict[str, int]:
        return {}

    monkeypatch.setattr(
        reconcile.db_documents, "list_document_paths", list_document_paths
    )

    report = await reconcile.reconcile_store(user_store)

    assert report.entries_ingested == 1
    assert "host = local" in (workspace_dir / "settings.md").read_text()
    # A converter run has no place in a sweep that blocks the server's boot.
    assert not (workspace_dir / "table.md").exists()
    assert not (workspace_dir / "loose.md").exists()


async def test_reconcile_skips_a_failing_entry_and_ingests_the_rest(
    user_store: Casebase, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace_dir / "good.md").write_text("ok")
    (workspace_dir / "bad.md").write_text("boom")

    async def chunk_and_index(
        store: Casebase, filename: str, *args: object, **kwargs: object
    ) -> None:
        if filename == "bad.md":
            raise RuntimeError("chunker exploded")

    async def list_document_paths(store: Casebase) -> dict[str, int]:
        return {}

    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", chunk_and_index)
    monkeypatch.setattr(
        reconcile.db_documents, "list_document_paths", list_document_paths
    )

    # One poison file must not abort the batch: the healthy entry still ingests.
    report = await reconcile.reconcile_store(user_store)

    assert report.entries_ingested == 1


async def test_reconcile_drops_rows_whose_description_vanished(
    user_store: Casebase, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def list_document_paths(store: Casebase) -> dict[str, int]:
        return {"ghost.md": 2}

    dropped: list[str] = []

    async def delete_documents(store: Casebase, paths: list[str]) -> int:
        dropped.extend(paths)
        return len(paths)

    monkeypatch.setattr(
        reconcile.db_documents, "list_document_paths", list_document_paths
    )
    monkeypatch.setattr(reconcile, "_delete_chunked_documents", delete_documents)

    report = await reconcile.reconcile_store(user_store)

    assert dropped == ["ghost.md"]
    assert report.sql_orphans_removed == 1
