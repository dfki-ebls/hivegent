"""Unit tests pinning the filesystem-as-source-of-truth reconcile policy.

The filesystem is authoritative for content: reconciliation ingests on-disk
markdown into SQL and drops rows whose description vanished, but it must
never delete workspace files — a non-markdown file without an owning entry
is inert content, not an orphan to prune.
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

    monkeypatch.setattr(db_documents, "get_entry_state", get_entry_state)
    monkeypatch.setattr(workspace.indexing, "chunk_and_index_document", chunk_and_index)
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
