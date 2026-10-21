"""Shared test fixtures for hivegent.

Every test in this tree is stateless: it touches only a temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
No test connects to a live database or any other stateful service.
"""

from pathlib import Path

import pytest
from pydantic_ai import models

from hivegent.agents import RunPrefix

# No test reaches a live model, so a request that would is an error, and a test
# serving one from a mock transport allows it explicitly.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a stored memory without reaching the database."""
    from hivegent.agents.tools import memory
    from hivegent.store import User

    async def load(_user: User) -> str:
        return "- likes tea"

    monkeypatch.setattr(memory, "load_memory", load)


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


@pytest.fixture()
def run_prefix() -> RunPrefix:
    """A run whose prefix stands in for a chat turn.

    Its model is the one thing a test swaps, which is what carrying the
    resolved model on the prefix buys: nothing has to reach for the shared
    HTTP client a real one would be built from.
    """
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models.function import FunctionModel

    from hivegent.agents import UserDeps, build_capabilities
    from hivegent.llm_config import LlmConfig
    from hivegent.store import User
    from hivegent.types import ToolsSpec

    return RunPrefix(
        deps=UserDeps(user=User("u"), mode="interactive"),
        capabilities=build_capabilities(ToolsSpec(), mode="interactive", language="en"),
        instructions="chat instructions",
        llm=LlmConfig(),
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[])),
    )
