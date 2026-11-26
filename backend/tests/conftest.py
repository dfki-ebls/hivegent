"""Shared test fixtures for hivegent.

Every test in this tree is stateless: it touches only a temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
No test connects to a live database or any other stateful service.
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import count
from pathlib import Path

# Settings resolve once on import, so the variables of a developer's shell,
# such as a live database URL, would otherwise reach every test.
for name in [name for name in os.environ if name.startswith("HIVEGENT_")]:
    del os.environ[name]

import pytest
from exar.memory import mounts
from pydantic_ai import models

from hivegent import pipelines
from hivegent.agents import RunPrefix

# No test reaches a live model, so a request that would is an error, and a test
# serving one from a mock transport allows it explicitly.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def _pipeline_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run offloaded pipeline work on threads, so no test spawns a process."""
    with ThreadPoolExecutor() as executor:
        monkeypatch.setattr(
            pipelines, "HOST", replace(pipelines.HOST, executor=executor)
        )
        yield


@pytest.fixture(autouse=True)
def _rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test full chat buckets, which a turn spends as it is prepared."""
    from hivegent.server.rate_limit import CHAT, RESUME, TokenBuckets

    for limit in (CHAT, RESUME):
        monkeypatch.setattr(limit, "buckets", TokenBuckets())


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a stored memory without reaching the database."""
    from exar.memory import InMemoryMemory

    from hivegent.agents.tools import memory
    from hivegent.store import User

    def stored(user: User) -> InMemoryMemory[str, str]:
        return InMemoryMemory(entries={user.id: "- likes tea"})

    monkeypatch.setattr(memory, "user_memory", stored)


@pytest.fixture(autouse=True)
def committed_transactions(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Stand for PostgreSQL's transaction ids, each fresh, and committed when a test says so."""
    from hivegent.db import changes

    ids = count()
    committed: set[str] = set()

    async def transaction_id(_s: object) -> str:
        return str(next(ids))

    async def is_committed(xid: str) -> bool:
        return xid in committed

    monkeypatch.setattr(changes, "transaction_id", transaction_id)
    monkeypatch.setattr(changes, "committed", is_committed)

    return committed


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``settings.data_dir`` at a temporary directory, for every test.

    So no test writes below the checkout, not even a changeset's journal.
    """
    from hivegent.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def folded_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the filesystem holding ``tmp_path`` fold case, as on macOS or Windows.

    Set in the per-device cache that every binding of ``folds_case`` consults,
    so exar's own path respelling sees it too.
    """
    monkeypatch.setitem(mounts._FOLDING, tmp_path.stat().st_dev, True)


@pytest.fixture()
def memory_store(monkeypatch: pytest.MonkeyPatch):
    """Keep conversations in pydantic-ai's in-memory store, without titles, for users who chose nothing."""
    from pydantic_ai.conversation_store import InMemoryConversationStore

    from hivegent.db.conversations import OwnedConversationStore, queries
    from hivegent.server import common
    from hivegent.server.routes.conversations import compaction

    class Store(OwnedConversationStore, InMemoryConversationStore):
        pass

    store = Store()

    async def untitled(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(queries, "store", store)
    monkeypatch.setattr(compaction, "store", store)

    async def unchosen(_user_id: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(queries, "set_conversation_title", untitled)
    monkeypatch.setattr(common, "load_preferences", unchosen)

    return store


@pytest.fixture()
def user_store(data_dir: Path):
    """Return a user workspace rooted in the temporary data directory."""
    from hivegent.store import HostGrant, UserScope

    _ = data_dir
    return HostGrant().workspace(UserScope("testuser"))


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

    deps = UserDeps(user=User("u"), mode="interactive")

    return RunPrefix(
        deps=deps,
        capabilities=build_capabilities(deps),
        instructions="chat instructions",
        llm=LlmConfig(model="test"),
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[])),
    )
