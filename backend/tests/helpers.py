"""Helpers shared across the test tree.

Fixtures live in ``conftest.py``; this is for the plain functions a test
calls directly.
"""

import io
import json
from collections.abc import AsyncGenerator, Awaitable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from exar.agents import session
from exar.ingest import Chunk
from exar.tools import BatchData, ItemFailure
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic import SecretStr
from pydantic_ai import RunContext
from pydantic_ai.conversation_store import ConversationEntry
from pydantic_ai.exceptions import ModelContextWindowExceededError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from hivegent.agents import UserDeps
from hivegent.config import parse_uuid
from hivegent.config.llm import LlmModelSettings, LlmProviderSettings, LlmSettings
from hivegent.db.conversations import OwnedConversation
from hivegent.db.conversations.models import _conversation
from hivegent.db.documents import EntryState
from hivegent.metadata import DocumentMetadata, EntryMetadata, EntryOrigin
from hivegent.security import OperatorUrl
from hivegent.store import User, Workspace
from hivegent.tools.entries import ContentStat, Part, stem_path_from_reference
from hivegent.tools.workspace_os import ChangesetLimits
from hivegent.types import FilterEntry

__all__ = [
    "LIMITS",
    "MEMORY_READ",
    "OVERFLOW",
    "ConversationRows",
    "FakeIndex",
    "FakeSession",
    "chain",
    "doc",
    "entry",
    "folder",
    "in_run",
    "owned",
    "png_bytes",
    "run_context",
    "single",
    "sse_chunks",
]

LIMITS = ChangesetLimits(max_operations=200, max_deletes=100, max_chars=20_000_000)
"""What one program may stage in a test that is not about the limits."""

OVERFLOW = ModelHTTPError.for_category(
    ModelContextWindowExceededError, status_code=400, model_name="m"
)
"""A context-window overflow as pydantic-ai raises it."""

MEMORY_READ: list[ModelMessage] = [
    ModelResponse(parts=[ToolCallPart("read_memory", {}, tool_call_id="memory")]),
    ModelRequest(
        parts=[ToolReturnPart("read_memory", "- likes tea", tool_call_id="memory")]
    ),
]
"""The read ``MEMORY_NOTE`` reports beside the first prompt of a window."""


def chain(*messages: ModelMessage) -> list[ConversationEntry]:
    """*messages* as the entries of one history, each the parent of the next."""
    entries: list[ConversationEntry] = []

    for message in messages:
        entries.append(
            ConversationEntry(
                parent_id=entries[-1].id if entries else None, message=message
            )
        )

    return entries


def doc(path: str) -> FilterEntry:
    """The filter entry of the document at *path*."""
    return FilterEntry(path=path, directory=False)


def folder(path: str) -> FilterEntry:
    """The filter entry of the directory at *path*, the empty local one being the whole store."""
    return FilterEntry(path=path, directory=True)


def sse_chunks(body: str) -> list[dict[str, Any]]:
    """The chunks of a Vercel AI stream *body*, without its closing ``[DONE]``."""
    return [
        json.loads(data)
        for event in body.split("\n\n")
        if (data := event.removeprefix("data: ").strip()) and data != "[DONE]"
    ]


def label_id(label: str) -> UUID:
    """The id a test names *label*, a UUID as a client mints it.

    >>> label_id("c1") == label_id("c1") != label_id("c2")
    True
    """
    return parse_uuid(label) or uuid5(NAMESPACE_URL, label)


async def in_run[T](deps: UserDeps, run: Awaitable[T]) -> T:
    """Awaits *run* as the root of an exar run under the config of *deps*, as a turn runs, whose policy the gate applies."""
    async with session("", deps.run_config, name="test", record=False):
        return await run


def owned(label: str, user: User | None = None) -> OwnedConversation:
    """The conversation named *label* (see :func:`label_id`) of *user*, ``u`` when none is given."""
    return _conversation((user or User("u")).id, label_id(label))


def png_bytes(info: PngInfo | None = None) -> bytes:
    """A small PNG, optionally carrying the chunks sanitisation strips."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 80, 160)).save(buffer, format="PNG", pnginfo=info)

    return buffer.getvalue()


def single[R](data: BatchData[R]) -> R:
    """The one item a one-item batch served, asserting it did not fail."""
    assert isinstance(data, tuple) and len(data) == 1
    item = data[0]
    assert not isinstance(item, ItemFailure)

    return item


def run_context[D](deps: D) -> RunContext[D]:
    """A run around *deps* whose model is never asked."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def catalog() -> LlmSettings:
    """A model catalog of one chat model beside a transcription one."""
    return LlmSettings(
        providers={
            "local": LlmProviderSettings(
                base_url=OperatorUrl("http://llmhop/v1"),
                api_key=SecretStr("operator-key"),
            )
        },
        models={
            "big": LlmModelSettings(provider="local", model="big-27b", max_tokens=4096),
            "whisper": LlmModelSettings(provider="local", model="whisper", chat=False),
        },
        default="big",
        stt="whisper",
    )


def entry(
    stem: str, original_suffix: str | None = None, *, origin: EntryOrigin = "upload"
) -> EntryMetadata:
    """The metadata of the entry at *stem*, with an original of *original_suffix* when it has one."""
    return EntryMetadata(
        entry_kind="user_markdown" if original_suffix is None else "convertible",
        stem_path=stem,
        description_path=Part(stem, "description").path,
        original_path=(
            None
            if original_suffix is None
            else Part(stem, "original", original_suffix).path
        ),
        assets_dir=None,
        origin=origin,
        generated_by="user" if original_suffix is None else "converter",
    )


@dataclass(slots=True, frozen=True)
class FakeSession:
    """The transaction a :class:`FakeIndex` hands out, which PostgreSQL knows by its id."""

    xid: str


@dataclass(slots=True)
class _Row:
    id: UUID
    state: EntryState


@dataclass(slots=True)
class FakeIndex:
    """The document tables, the chunk index, and the transactions of PostgreSQL, in memory.

    It stands in for :mod:`hivegent.db.documents` and the writes of
    :func:`hivegent.retrieval.index_document`, so the indexing of
    :func:`hivegent.chunks.chunk_and_index_document` runs for real and leaves
    its rows here, keyed by ``(store_key, stem_path)``.  Moves and deletes act on
    those rows, and a transaction records how it ended in :attr:`outcomes` and,
    by its id, in the status the journal asks for.

    Attributes:
        rows: Every row by its store key and stem.
        failing: The stems whose chunk write, or move, fails.
        indexed: The entries whose chunks were written, in order.
        outcomes: How each transaction ended, ``committed`` or ``rolled back``.
        status: Whether each transaction committed, by its id.
        drops: Where a connection dies at the end of each transaction, after its
            commit or before it, with the caller left to wonder.
    """

    status: dict[str, bool]
    rows: dict[tuple[str, str], _Row] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    indexed: list[EntryMetadata] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    drops: Literal["committed", "rolled back"] | None = None
    _moves: list[tuple[str, str, str, str, str]] = field(
        init=False, default_factory=list
    )
    _xids: count[int] = field(init=False, default_factory=count)

    def add(
        self,
        store: Workspace,
        metadata: EntryMetadata,
        *,
        digest: str | None = None,
        chunker: str | None = None,
        stat: ContentStat | None = None,
    ) -> None:
        """Hold a row for *metadata*, never indexed unless it has a *digest*."""
        key = store.store_key, metadata.stem_path
        state = EntryState(digest, chunker, stat, metadata)
        self.rows[key] = _Row(self.rows[key].id if key in self.rows else uuid4(), state)

    def stems(self, store: Workspace) -> list[str]:
        """The stems of the rows of *store*, sorted."""
        return sorted(stem for key, stem in self.rows if key == store.store_key)

    @property
    def moves(self) -> list[tuple[str, str, str, str, str]]:
        """Every row move as ``(kind, source, destination, source store, destination store)``.

        A moved row waits on a temporary stem between leaving and landing, so
        the two halves are composed back into the move they make.
        """
        parked: dict[str, tuple[str, str]] = {}
        moves: list[tuple[str, str, str, str, str]] = []

        for kind, src, dst, src_key, dst_key in self._moves:
            if ".changeset-park" in dst:
                parked[dst] = (src, src_key)
            else:
                origin, origin_key = parked.pop(src, (src, src_key))
                moves.append((kind, origin, dst, origin_key, dst_key))

        return moves

    def moved(self) -> list[tuple[str, str, str]]:
        """The moves as sorted ``(kind, source, destination)``."""
        return sorted((kind, src, dst) for kind, src, dst, *_stores in self.moves)

    @asynccontextmanager
    async def session(
        self, joined: object | None = None
    ) -> AsyncGenerator[FakeSession]:
        """A transaction, recording how it ended and failing as :attr:`drops` says."""
        _ = joined
        transaction = FakeSession(str(next(self._xids)))

        try:
            yield transaction
        except BaseException:
            self.outcomes.append("rolled back")
            self.status[transaction.xid] = False
            raise

        landed = self.drops != "rolled back"
        self.outcomes.append("committed" if landed else "rolled back")
        self.status[transaction.xid] = landed

        if self.drops:
            raise ConnectionError("acknowledgement lost")

    async def transaction_id(self, s: FakeSession) -> str:
        """The id of the transaction *s*."""
        return s.xid

    async def get_entry_metadata(
        self, store: Workspace, reference: str
    ) -> EntryMetadata | None:
        """The metadata of the row of *reference*, if it has one."""
        row = self.rows.get((store.store_key, stem_path_from_reference(reference)))

        return row.state.metadata if row else None

    async def get_entries_metadata(
        self, references: Iterable[tuple[Workspace, str]]
    ) -> dict[tuple[Workspace, str], EntryMetadata]:
        """The metadata of every row among *references*, by store and stem."""
        found: dict[tuple[Workspace, str], EntryMetadata] = {}

        for store, reference in references:
            stem = stem_path_from_reference(reference)

            if row := self.rows.get((store.store_key, stem)):
                found[store, stem] = row.state.metadata

        return found

    async def get_entry_state(
        self, store: Workspace, reference: str
    ) -> EntryState | None:
        """The state of the row of *reference*, if it has one."""
        row = self.rows.get((store.store_key, stem_path_from_reference(reference)))

        return row.state if row else None

    async def get_entry_states(self, store: Workspace) -> dict[str, EntryState]:
        """The state of every row of *store* by its description path."""
        return {
            Part(stem, "description").path: row.state
            for (key, stem), row in self.rows.items()
            if key == store.store_key
        }

    async def update_entry(
        self, store: Workspace, metadata: EntryMetadata, stat: ContentStat | None
    ) -> bool:
        """Refresh the metadata and stat of a row, whether it exists."""
        row = self.rows.get((store.store_key, metadata.stem_path))

        if row is not None:
            row.state = replace(row.state, metadata=metadata, content_stat=stat)

        return row is not None

    async def upsert_document(
        self, store: Workspace, metadata: EntryMetadata, pipeline: str, line_count: int
    ) -> DocumentMetadata:
        """Hold a row for *metadata* not yet indexed, keeping the id of one it replaces."""
        _ = line_count
        self.add(store, metadata)
        row = self.rows[store.store_key, metadata.stem_path]

        return DocumentMetadata(
            **metadata.model_dump(),
            id=row.id,
            pipeline=pipeline,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            chunks=[],
        )

    async def index_document(self, document_id: UUID, chunks: Sequence[Chunk]) -> None:
        """Write the chunks of a document, which fails for a stem of :attr:`failing`."""
        _ = chunks
        metadata = self._row(document_id).state.metadata

        if metadata.stem_path in self.failing:
            raise RuntimeError("chunks failed to write")

        self.indexed.append(metadata)

    async def set_content_state(
        self,
        document_id: UUID,
        content_digest: str,
        chunker: str,
        stat: ContentStat | None,
    ) -> None:
        """Stamp a document as indexed."""
        row = self._row(document_id)
        row.state = replace(
            row.state,
            content_digest=content_digest,
            chunker=chunker,
            content_stat=stat,
        )

    async def delete_document(
        self, store: Workspace, reference: str, *, s: object | None = None
    ) -> bool:
        """Drop the row of *reference*, whether it had one."""
        _ = s

        return (
            self.rows.pop((store.store_key, stem_path_from_reference(reference)), None)
            is not None
        )

    async def delete_subtree(
        self, store: Workspace, prefix: str, *, s: object | None = None
    ) -> int:
        """Drop every row strictly below *prefix*, and count them."""
        _ = s
        below = [
            key
            for key in self.rows
            if prefix and key[0] == store.store_key and key[1].startswith(f"{prefix}/")
        ]

        for key in below:
            del self.rows[key]

        return len(below)

    async def move_document(
        self,
        src_store: Workspace,
        src: str,
        dst_store: Workspace,
        dst: str,
        *,
        s: object | None = None,
    ) -> bool:
        """Re-key the row of *src*, which fails for a stem of :attr:`failing`."""
        _ = s

        if src in self.failing:
            raise RuntimeError("row failed to move")

        self._moves.append(
            ("move_document", src, dst, src_store.store_key, dst_store.store_key)
        )
        row = self.rows.pop((src_store.store_key, src), None)

        if row is None:
            return False

        self._land(row, dst_store, dst)

        return True

    async def move_subtree(
        self,
        src_store: Workspace,
        src: str,
        dst_store: Workspace,
        dst: str,
        *,
        s: object | None = None,
    ) -> None:
        """Re-key every row strictly below *src*."""
        _ = s
        self._moves.append(
            ("move_subtree", src, dst, src_store.store_key, dst_store.store_key)
        )

        below = [
            key
            for key in self.rows
            if key[0] == src_store.store_key and key[1].startswith(f"{src}/")
        ]

        for key in below:
            self._land(self.rows.pop(key), dst_store, dst + key[1][len(src) :])

    def _land(self, row: _Row, store: Workspace, stem: str) -> None:
        metadata = row.state.metadata
        suffix = (
            None
            if metadata.original_path is None
            else metadata.original_path.removeprefix(metadata.stem_path)
        )
        moved = entry(stem, suffix, origin=metadata.origin)
        row.state = replace(
            row.state,
            metadata=metadata.model_copy(
                update={
                    "stem_path": stem,
                    "description_path": moved.description_path,
                    "original_path": moved.original_path,
                    "assets_dir": (
                        None
                        if metadata.assets_dir is None
                        else Part(stem, "assets").path
                    ),
                }
            ),
        )
        self.rows[store.store_key, stem] = row

    def _row(self, document_id: UUID) -> _Row:
        return next(row for row in self.rows.values() if row.id == document_id)


@dataclass(slots=True)
class ConversationRows:
    """The conversation rows that file cleanup removes or replaces, which stand in for PostgreSQL's.

    Each removal records the id of its conversation or owner with whether the
    folder it names was still on disk, and so does each import, which fails
    with ``import_error`` while that is set.
    """

    data_dir: Path
    removed: list[tuple[str, bool]] = field(default_factory=list)
    imported: list[bool] = field(default_factory=list)
    import_error: Exception | None = None
