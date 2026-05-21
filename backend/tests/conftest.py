"""Shared test fixtures for hivegent."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hivegent.chunkers import ChunkingPipeline, ChunkingSpec
from hivegent.store import Casebase
from hivegent.types import PipelineSpec

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fake_embeddings(values: Sequence[str]) -> list[list[float]]:
    """Return stable tiny embeddings for tests."""
    return [[float(len(value)), float(index)] for index, value in enumerate(values)]


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``settings.data_dir`` at a temporary directory.

    Since every module does ``from .config import settings`` and holds a
    reference to the *same* ``Settings`` instance, mutating its attributes
    is visible everywhere — no need for ``from . import config``.
    """
    from hivegent.config import settings
    from hivegent.retrieval import _RetrievalState

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("hivegent.retrieval._state", _RetrievalState())
    return tmp_path


@pytest.fixture()
def db_initialized(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Rebuild the async engine against the temporary data dir and create tables.

    The module-level engine is bound at import time, so tests that touch
    SQL need to swap it for one pointed at the tmp_path SQLite file.
    """
    import asyncio
    import importlib

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from hivegent.db.engine import _build_engine
    from hivegent.db.models import Base

    engine_mod = importlib.import_module("hivegent.db.engine")
    new_engine = _build_engine()
    new_sessionmaker = async_sessionmaker(new_engine, expire_on_commit=False)
    monkeypatch.setattr(engine_mod, "engine", new_engine)
    monkeypatch.setattr(engine_mod, "Session", new_sessionmaker)

    async def _create_all() -> None:
        async with new_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    yield
    asyncio.run(new_engine.dispose())


@pytest.fixture()
def user_store(data_dir: Path) -> Casebase:
    """Return a user casebase rooted in the temporary data directory."""
    return Casebase(kind="user", id="testuser")


@pytest.fixture()
def fake_embeddings(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fake embeddings on the retrieval singleton."""
    import hivegent.retrieval as retrieval

    _ = data_dir
    monkeypatch.setattr(retrieval._state, "_embedding_func", _fake_embeddings)


@pytest.fixture()
def single_chunk_pipeline() -> PipelineSpec:
    """Return a pipeline spec that avoids heavyweight chunkers."""
    return PipelineSpec(chunking=ChunkingSpec(pipeline=ChunkingPipeline.NONE))


@pytest.fixture()
def app_client(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a Starlette ``TestClient`` with auth disabled.

    Used as a context manager so the FastAPI lifespan runs — required for
    the shared HTTP client (and any other lifespan-owned resource) to be
    initialised. SSRF policy is independent of auth: tests upload with a
    localhost LLM base_url, so the SSRF filter must be opened explicitly
    here.
    """
    from hivegent.config import settings

    monkeypatch.setattr(settings.auth, "enable", False)
    monkeypatch.setattr(settings.auth, "allow_disabled", True)
    monkeypatch.setattr(settings.security, "allow_private_urls", True)

    from starlette.testclient import TestClient

    from hivegent.server.app import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def annotations() -> list[dict[str, Any]]:
    """Load the sample annotations fixture."""
    path = FIXTURES_DIR / "annotations.json"
    return json.loads(path.read_text(encoding="utf-8"))
