"""Unit tests for the three-store reconciler."""

from pathlib import Path

import hivegent.retrieval as retrieval
import hivegent.workspace as workspace
from hivegent.db import documents as db_documents
from hivegent.reconcile import _owning_stem_for_path, reconcile_store
from hivegent.retrieval import _build_key
from hivegent.store import Casebase
from hivegent.types import PipelineSpec


class TestOwningStem:
    """``.assets`` ownership resolution for the disk-orphan sweep."""

    def test_plain_file(self) -> None:
        from pathlib import PurePosixPath

        assert _owning_stem_for_path(PurePosixPath("docs/report.md")) == "docs/report"

    def test_inside_assets(self) -> None:
        from pathlib import PurePosixPath

        path = PurePosixPath("docs/report.assets/img.png")
        assert _owning_stem_for_path(path) == "docs/report"

    def test_nested_assets_collapse_to_outer(self) -> None:
        from pathlib import PurePosixPath

        path = PurePosixPath("a/b.assets/c.assets/d.png")
        assert _owning_stem_for_path(path) == "a/b"


async def test_clean_state_is_noop(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
    data_dir: Path,
) -> None:
    """A consistent state reports zero work."""
    _ = (db_initialized, fake_embeddings, data_dir)
    await workspace.upload(
        user_store, "report.md", b"content", spec=single_chunk_pipeline
    )

    report = await reconcile_store(user_store)

    assert report.disk_orphans_removed == 0
    assert report.sql_orphans_removed == 0
    assert report.lance_orphans_removed == 0
    assert report.lance_reindexed == 0


async def test_disk_orphan_removed(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    data_dir: Path,
) -> None:
    """A bare file on disk with no SQL row is deleted."""
    _ = (db_initialized, fake_embeddings)
    workspace_dir = user_store.workspace_dir(data_dir)
    stray = workspace_dir / "stray.md"
    stray.write_text("orphan", encoding="utf-8")
    assert stray.exists()

    report = await reconcile_store(user_store)

    assert report.disk_orphans_removed == 1
    assert not stray.exists()


async def test_sql_orphan_removed_when_file_missing(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
    data_dir: Path,
) -> None:
    """A SQL document whose disk file vanished is dropped from SQL + LanceDB."""
    _ = (db_initialized, fake_embeddings)
    await workspace.upload(
        user_store, "ghost.md", b"vanishing", spec=single_chunk_pipeline
    )
    workspace_dir = user_store.workspace_dir(data_dir)
    (workspace_dir / "ghost.md").unlink()

    report = await reconcile_store(user_store)

    assert report.sql_orphans_removed == 1
    assert await db_documents.get_document(user_store, "ghost.md") is None
    storage = retrieval._state.get_storage()
    assert storage.index == {}


async def test_lance_orphan_removed_without_sql(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
    data_dir: Path,
) -> None:
    """LanceDB rows without a SQL counterpart are unindexed."""
    _ = (db_initialized, fake_embeddings, data_dir)
    await workspace.upload(
        user_store, "doc.md", b"payload", spec=single_chunk_pipeline
    )
    # Pull SQL out from under LanceDB.
    assert await db_documents.delete_document(user_store, "doc.md")
    storage = retrieval._state.get_storage()
    assert _build_key(user_store, "doc.md", 0) in storage.index

    report = await reconcile_store(user_store)

    assert report.lance_orphans_removed == 1
    assert storage.index == {}


async def test_lance_reindexed_when_missing(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
    data_dir: Path,
) -> None:
    """A SQL document with chunks but no LanceDB rows is re-indexed."""
    _ = (db_initialized, fake_embeddings, data_dir)
    await workspace.upload(
        user_store, "doc.md", b"content", spec=single_chunk_pipeline
    )
    await retrieval.unindex_paths(user_store, ["doc.md"])
    storage = retrieval._state.get_storage()
    assert storage.index == {}

    report = await reconcile_store(user_store)

    assert report.lance_reindexed == 1
    assert _build_key(user_store, "doc.md", 0) in storage.index


async def test_asset_file_under_known_stem_is_preserved(
    db_initialized: None,
    user_store: Casebase,
    fake_embeddings: None,
    single_chunk_pipeline: PipelineSpec,
    data_dir: Path,
) -> None:
    """Raw asset files under ``foo.assets/`` survive when ``foo`` is in SQL."""
    _ = (db_initialized, fake_embeddings)
    await workspace.upload(
        user_store, "report.md", b"body", spec=single_chunk_pipeline
    )
    workspace_dir = user_store.workspace_dir(data_dir)
    asset = workspace_dir / "report.assets" / "image.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"fake png")

    report = await reconcile_store(user_store)

    assert report.disk_orphans_removed == 0
    assert asset.exists()
