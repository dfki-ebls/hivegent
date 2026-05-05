"""Shared test fixtures for hivegent."""

import json
from pathlib import Path
from typing import Any

import pytest

from hivegent.store import Casebase

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
def user_store(data_dir: Path) -> Casebase:
    """Return a user casebase rooted in the temporary data directory."""
    return Casebase(kind="user", id="testuser")


@pytest.fixture()
def app_client(data_dir: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Return a Starlette ``TestClient`` with auth disabled."""
    from hivegent.config import settings

    monkeypatch.setattr(settings.auth, "disabled", True)

    from starlette.testclient import TestClient

    from hivegent.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def annotations() -> list[dict[str, Any]]:
    """Load the sample annotations fixture."""
    path = FIXTURES_DIR / "annotations.json"
    return json.loads(path.read_text(encoding="utf-8"))
