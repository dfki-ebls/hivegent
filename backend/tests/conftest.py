"""Shared test fixtures for hivegent.

Every test in this tree is stateless: it touches only a temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
No test connects to a live database or any other stateful service.
"""

from pathlib import Path

import pytest


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
