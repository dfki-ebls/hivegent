"""Shared test fixtures for hivegent.

Every test in this tree is stateless: it touches only a temporary
filesystem and module-level Python state guarded by ``monkeypatch``.
No test connects to a live database or any other stateful service.
"""

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from tests.helpers import ConversationRows, FakeIndex

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
def transaction_status(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Stand for ``pg_xact_status``, whether each transaction committed by its id.

    A test records the fate of a transaction, and one it leaves out is forgotten by PostgreSQL.
    """
    from hivegent.db import changes

    status: dict[str, bool] = {}

    async def committed(xid: str) -> bool | None:
        return status.get(xid)

    monkeypatch.setattr(changes, "committed", committed)

    return status


@pytest.fixture()
def index(
    monkeypatch: pytest.MonkeyPatch, transaction_status: dict[str, bool]
) -> FakeIndex:
    """Replace the document tables, the chunk index, and the transactions of PostgreSQL by one in-memory fake.

    Indexing, moving, and deleting run for real and leave their rows in it.
    """
    from hivegent import retrieval
    from hivegent.db import changes, engine
    from hivegent.db import documents as db_documents

    fake = FakeIndex(transaction_status)

    for name in (
        "get_entry_metadata",
        "get_entries_metadata",
        "get_entry_state",
        "get_entry_states",
        "update_entry",
        "upsert_document",
        "set_content_state",
        "delete_document",
        "delete_subtree",
        "move_document",
        "move_subtree",
    ):
        monkeypatch.setattr(db_documents, name, getattr(fake, name))

    monkeypatch.setattr(retrieval, "index_document", fake.index_document)
    monkeypatch.setattr(engine, "session", fake.session)
    monkeypatch.setattr(changes, "transaction_id", fake.transaction_id)

    return fake


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


type Configure = Callable[..., None]


@pytest.fixture()
def configure(monkeypatch: pytest.MonkeyPatch) -> Configure:
    """Change the global settings by name, a section given as a dict of the fields to change.

    ``configure(tools={"disabled": ["grep"]}, accept_terms=True)`` replaces
    ``settings.tools`` by a copy with its ``disabled`` field changed, so every
    change is undone with the test.
    """
    from pydantic import BaseModel

    from hivegent.config import settings

    def apply(**changes: object) -> None:
        for name, change in changes.items():
            current = getattr(settings, name)

            if isinstance(current, BaseModel) and isinstance(change, dict):
                change = current.model_copy(update=change)

            monkeypatch.setattr(settings, name, change)

    return apply


type UsePrefix = Callable[[RunPrefix], None]


@pytest.fixture()
def use_prefix(monkeypatch: pytest.MonkeyPatch) -> UsePrefix:
    """Make the chat turns that follow run on a given prefix, instead of one resolved from the preferences and the settings."""
    from hivegent.server.routes.conversations import turn

    def use(prefix: RunPrefix) -> None:
        async def build_run_prefix(*_args: object) -> RunPrefix:
            return prefix

        monkeypatch.setattr(turn, "build_run_prefix", build_run_prefix)

    return use


@pytest.fixture()
def conversation_rows(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> ConversationRows:
    """Keep the rows of the conversations file cleanup removes or imports out of the database."""
    from datetime import UTC, datetime

    from hivegent import conversation_files
    from hivegent.db.conversations import ConversationSummary, OwnedConversation
    from hivegent.store import User

    rows = ConversationRows(data_dir)

    async def remove_conversation(conversation: OwnedConversation) -> bool:
        rows.removed.append(
            (str(conversation.id), conversation.folder(data_dir).exists())
        )

        return True

    async def remove_owned_conversations(user: User) -> None:
        rows.removed.append(
            (user.id, OwnedConversation.user_root(data_dir, user.id).exists())
        )

    async def import_conversation(
        conversation: OwnedConversation, *_args: object, **_kwargs: object
    ) -> ConversationSummary:
        rows.imported.append(conversation.folder(data_dir).exists())

        if rows.import_error is not None:
            raise rows.import_error

        moment = datetime(2026, 1, 1, tzinfo=UTC)

        return ConversationSummary(
            id=conversation.id, title="", created_at=moment, updated_at=moment
        )

    monkeypatch.setattr(conversation_files, "remove_conversation", remove_conversation)
    monkeypatch.setattr(
        conversation_files, "remove_owned_conversations", remove_owned_conversations
    )
    monkeypatch.setattr(conversation_files, "import_conversation", import_conversation)

    return rows
