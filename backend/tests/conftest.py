"""Shared test fixtures for hivegent.

Every test in this tree is stateless: it touches only a temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
No test connects to a live database or any other stateful service.
"""

from pathlib import Path

import pytest
from pydantic_ai import models

# No test reaches a live model, so a request that would is an error, and a test
# serving one from a mock transport allows it explicitly.
models.ALLOW_MODEL_REQUESTS = False


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
    return Casebase.for_user("testuser")
