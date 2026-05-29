"""Shared test fixtures for hivegent.

Unit tests in this tree are hermetic: they touch only the temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
Integration tests under ``tests/integration/`` drive the FastAPI app via
``app_client`` and exercise the real Alembic/Postgres path, and eval
tests under ``tests/evals/`` are marked ``slow``; both require a running
Postgres.
"""

import json
from pathlib import Path
from typing import Any

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``settings.data_dir`` at a temporary directory."""
    from hivegent.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def user_store(data_dir: Path):
    """Return a user casebase rooted in the temporary data directory."""
    from hivegent.store import Casebase

    _ = data_dir
    return Casebase(kind="user", id="testuser")


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
