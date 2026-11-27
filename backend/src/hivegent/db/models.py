# SQLAlchemy 2.0 ``Mapped[T] = mapped_column(...)`` declarations are initialized
# by the ORM metaclass at runtime, which basedpyright cannot see through, so it
# flags every column as uninitialized.  Scoped to this module: it is the only
# ORM-shaped one, and elsewhere the check is worth keeping.
# pyright: reportUninitializedInstanceVariable=false

"""SQLAlchemy 2.0 schema for Hivegent.

Single module, small enough to read top-to-bottom.  Repositories convert
these ORM rows to Pydantic models at their public surface; nothing
outside :mod:`hivegent.db` sees an ORM object.

The PostgreSQL/pgvector ``chunks`` table holds chunk metadata, text,
and vectors in one normalised row.  cbrkit searches, re-embeds, and
writes it through :class:`cbrkit.indexable.Pgvector` with this
model, which embeds a batch before it replaces a document's rows in one
transaction (see :mod:`hivegent.retrieval`).
"""

import enum
from datetime import UTC, datetime
from typing import Any, ClassVar, override
from uuid import UUID, uuid4

import sqlalchemy as sa
from cbrkit.indexable import PGVECTOR, TSVECTOR, tsvector_computed
from pydantic import TypeAdapter
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage
from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    cast,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)
from sqlalchemy.types import DateTime, TypeDecorator, TypeEngine, Uuid

from ..config import settings

__all__ = [
    "ApplicationSettings",
    "Base",
    "Chunk",
    "Conversation",
    "Document",
    "EntryKind",
    "GeneratedBy",
    "Group",
    "JSONText",
    "McpConfirmation",
    "Memory",
    "Message",
    "Origin",
    "PydanticJSONB",
    "Source",
    "SourceItem",
    "StoreId",
    "Timestamped",
    "User",
    "Workspace",
]


# ─── Base ──────────────────────────────────────────────────────────────


_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


type _TypeEngineArg = type[TypeEngine[Any]] | TypeEngine[Any]
"""What a ``type_annotation_map`` entry maps to: an engine class or instance."""


class PydanticJSONB[T](TypeDecorator[T]):
    """JSONB holding a value of a pydantic type, dumped on write and validated on read, ``None`` as SQL ``NULL``."""

    impl = JSONB
    cache_ok = True

    def __init__(self, adapter: TypeAdapter[T]) -> None:
        super().__init__(none_as_null=True)
        self.adapter = adapter

    @override
    def process_bind_param(self, value: T | None, dialect: Dialect) -> Any:
        return None if value is None else self.adapter.dump_python(value, mode="json")

    @override
    def process_result_value(self, value: Any, dialect: Dialect) -> T | None:
        return None if value is None else self.adapter.validate_python(value)


class JSONText(TypeDecorator[str]):
    """JSONB read and written as JSON text, so a caller parses it once and the driver never does."""

    impl = JSONB
    cache_ok = True

    @override
    def bind_processor(self, dialect: Dialect) -> None:
        return None

    @override
    def result_processor(self, dialect: Dialect, coltype: object) -> None:
        return None

    @override
    def column_expression(self, column: ColumnElement[str]) -> ColumnElement[str]:
        return cast(column, Text)


type StoreId = str
"""An id pydantic-ai's conversation store names as text, a UUID stored as ``uuid``."""


class Base(DeclarativeBase):
    """Typed declarative base with portable annotation map."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, _TypeEngineArg]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        StoreId: Uuid(as_uuid=False),
        list[StoreId]: ARRAY(Uuid(as_uuid=False)),
        RunUsage: PydanticJSONB(TypeAdapter(RunUsage)),
        DeferredToolRequests: PydanticJSONB(TypeAdapter(DeferredToolRequests)),
    }


def _now() -> datetime:
    return datetime.now(UTC)


class Timestamped:
    """Mixin adding ``created_at`` / ``updated_at`` columns.

    Both carry a ``server_default`` of ``now()`` so a row inserted outside
    the ORM (raw SQL, an operator ``psql`` session, a data-backfill
    migration) is still stamped instead of failing the ``NOT NULL``.
    ``updated_at`` is bumped on every UPDATE via the app-side ``onupdate``;
    callers only need to assign it explicitly when they want to mark a row
    as modified without changing any of its other columns (e.g. a parent
    whose only "change" is an inserted child).
    """

    created_at: Mapped[datetime] = mapped_column(
        default=_now, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=_now, onupdate=_now, server_default=sa.func.now()
    )


def _enum(t: type[enum.StrEnum]) -> Enum:
    """Native Postgres ENUM column backed by *t*.

    Postgres-only schema, so we use real ``CREATE TYPE`` enums instead of
    ``VARCHAR + CHECK``, so values live in one place (the type) rather than
    being duplicated in a per-column check constraint.

    ``values_callable`` makes the column store each member's ``value`` (e.g.
    ``binary_stub``) rather than SQLAlchemy's default of the member ``name``
    (``BINARY_STUB``), matching the lowercase labels in the ``CREATE TYPE``.
    """
    return Enum(
        t,
        name=t.__name__.lower(),
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


# ─── Enums ─────────────────────────────────────────────────────────────


class EntryKind(enum.StrEnum):
    USER_MARKDOWN = "user_markdown"
    IMAGE = "image"
    VIDEO = "video"
    CONVERTIBLE = "convertible"
    BINARY_STUB = "binary_stub"


class Origin(enum.StrEnum):
    UPLOAD = "upload"
    COLLECTION = "collection"
    EXTRACTED = "extracted"
    # Discovered on disk by the reconciler with no prior row (hand-dropped
    # files, and the future read-write shell tool's fold-back).
    IMPORTED = "imported"
    # Mirrored from an external source by its sync.
    SYNCED = "synced"


class GeneratedBy(enum.StrEnum):
    USER = "user"
    CONVERTER = "converter"
    VISION = "vision"
    STUB = "stub"


# ─── Identity ──────────────────────────────────────────────────────────


class User(Timestamped, Base):
    """A user that has left a footprint in the local database.

    Identity attributes (email, display name) and group membership are
    not stored: they live solely in the OIDC token and are reconstructed
    per request.  A row here is just the anchor that owns conversations,
    a workspace, and memory, and keeps the choices the user made for their
    runs, only those they set, so a default the operator changes reaches
    everyone who never chose (:class:`~hivegent.types.Preferences`).
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    preferences: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default="{}"
    )

    memory: Mapped[Memory | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Group(Timestamped, Base):
    """A group that owns a shared workspace.

    Like :class:`User`, membership is an OIDC concern and is never
    persisted; a row here only anchors the group's workspace.
    """

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(primary_key=True)


class Source(Base):
    """An external source's sync state, all of it rebuilt by a full sync.

    A row anchors the mirror's workspace and its items, and goes with them
    once the source leaves the configuration.  ``cursor`` is where the next
    delta starts, ``synced_at`` when the last sync finished, and ``error``
    why it failed, if it did.  ``requested`` is the sync an admin asked for
    and no sync has finished yet, and ``request_seq`` counts the requests,
    so a sync clears only the request it answered.
    """

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(primary_key=True)
    cursor: Mapped[str | None]
    synced_at: Mapped[datetime | None]
    error: Mapped[str | None]
    requested: Mapped[str | None]
    request_seq: Mapped[int] = mapped_column(default=0, server_default="0")


class SourceItem(Base):
    """Which file of a source lies where in its mirror, at which version.

    Keyed by the source's own id for the file, so a rename is a move and a
    deletion names exactly the file it removes.  A ``held`` file is gone from
    the source but stays mirrored until an admin confirms its deletion.
    """

    __tablename__ = "source_items"
    __table_args__ = (UniqueConstraint("source_id", "path"),)

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    remote_id: Mapped[str] = mapped_column(primary_key=True)
    path: Mapped[str]
    version: Mapped[str]
    held: Mapped[bool] = mapped_column(default=False, server_default=sa.false())


# ─── Memory ────────────────────────────────────────────────────────────


class Memory(Timestamped, Base):
    __tablename__ = "memory"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str]

    user: Mapped[User] = relationship(back_populates="memory")


# ─── Conversations ─────────────────────────────────────────────────────


class Conversation(Timestamped, Base):
    """A conversation, the record a :class:`~pydantic_ai.conversation_store.ConversationStore` keeps.

    The record columns mirror ``ConversationRecord`` in its own types, so a
    row and a record convert field by field: ``head_id`` is the
    newest message of the active history, ``branches`` the last messages of
    the histories it left, ``deferred_tool_requests`` the calls a paused run
    awaits, ``usage`` what every run spent, and ``revision`` the token a save
    checks, so a concurrent writer cannot overwrite it unnoticed.  The owner
    is the store's ``scope``, and the title is the app's own.  ``head_id``
    carries no foreign key, so the two tables do not reference each other in
    a cycle, and the store checks it names a stored message.
    """

    __tablename__ = "conversations"
    __table_args__ = (Index(None, "user_id", "updated_at"),)

    # Chosen by the client, so it has no default.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision: Mapped[str]
    title: Mapped[str | None]
    head_id: Mapped[StoreId | None]
    branches: Mapped[list[StoreId]] = mapped_column(default=list, server_default="{}")
    usage: Mapped[RunUsage] = mapped_column(server_default="{}")
    deferred_tool_requests: Mapped[DeferredToolRequests | None]
    forked_from: Mapped[StoreId | None]
    forked_at: Mapped[StoreId | None]

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(Base):
    """One message of a conversation, a ``ConversationEntry`` of its store.

    A message points at the one before it, so a conversation's messages form
    a tree whose active history its record's ``head_id`` names.  Messages are
    never changed or moved once stored: editing or regenerating a turn stores
    the new turn beside the old one, which stays a branch.  The id is minted
    by the store, apart from the id the client knows the message by, which
    rides in the message's own metadata.  The payload never holds the
    instructions a request was sent under: ``instructions`` does where they
    differ from the ones in effect at its parent, ``''`` for none, which
    pydantic-ai never sends as text, and every message below inherits them.
    """

    __tablename__ = "messages"
    __table_args__ = (Index(None, "conversation_id"), Index(None, "parent_id"))

    id: Mapped[StoreId] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    parent_id: Mapped[StoreId | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    payload: Mapped[str] = mapped_column(JSONText)
    instructions: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        default=_now, server_default=sa.func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ─── Workspaces, documents & chunks ────────────────────────────────────


class Workspace(Base):
    """The workspace of one scope, keyed by the scope's key, which every document names.

    A user's, a group's, or a source's names its parent row, which takes it
    along when deleted, and the global one names none.  The key is the one
    its parent derives, so a row can neither claim another scope's key nor
    hang below a second parent.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, group_id, source_id) <= 1 AND key = "
            "coalesce('user:' || user_id, 'group:' || group_id, "
            "'source:' || source_id, 'global')",
            name="parent",
        ),
    )

    key: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE")
    )
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )


class Document(Timestamped, Base):
    """A logical stem entry inside a workspace, unique by its stem there.

    Path columns hold only the irreducible bits.  ``description_path``,
    ``original_path`` and ``assets_dir`` are derived from ``stem_path``,
    ``original_suffix`` and ``has_assets`` at the repository layer.
    """

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("workspace_key", "stem_path"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_key: Mapped[str] = mapped_column(
        ForeignKey("workspaces.key", ondelete="CASCADE")
    )

    stem_path: Mapped[str]
    # The original file's pathlib suffix, dot included (``.pdf``); ``""`` for an
    # extension-less or dotfile original whose path is the bare stem; ``None``
    # when the entry has no original.  ``original_path`` is reconstructed from
    # ``stem_path`` + this in the repository layer.
    original_suffix: Mapped[str | None]
    has_assets: Mapped[bool] = mapped_column(default=False)

    entry_kind: Mapped[EntryKind] = mapped_column(_enum(EntryKind))
    origin: Mapped[Origin] = mapped_column(_enum(Origin))
    generated_by: Mapped[GeneratedBy] = mapped_column(_enum(GeneratedBy))

    pipeline: Mapped[str]
    # The digest of the indexed text and the fingerprint of the chunker that
    # made its chunks (`exar.ingest.fingerprint`), stamped together after the
    # chunks are durable, so either differing from the current one marks the
    # row for re-indexing.
    content_digest: Mapped[str | None]
    chunker: Mapped[str | None]
    # The fingerprint of the converter the original was routed to for the
    # markdown (`exar.ingest.fingerprint`), none for markdown no converter made.
    converter: Mapped[str | None]
    # Line count of the markdown, written eagerly at row insert (a pure function
    # of the content) and surfaced to the frontend so a coverage map can place
    # partial reads without re-reading the file.
    line_count: Mapped[int]
    # ``(mtime_ns, size, inode)`` of the indexed markdown: a stat fast-path that
    # lets the reconciler skip re-reading a description whose stat is unchanged.
    content_mtime_ns: Mapped[int | None] = mapped_column(sa.BigInteger())
    content_size: Mapped[int | None] = mapped_column(sa.BigInteger())
    content_inode: Mapped[int | None] = mapped_column(sa.BigInteger())


# ─── Chunks (metadata + text + vector, one row each) ──────────────────


# Schema dim is the plain configured ``HIVEGENT_EMBEDDING__DIMENSION``
# (never a model probe), so importing this module for Alembic
# autogenerate, the server, or unit tests stays offline.
# ``reconcile_index_state`` fits the live column to it at boot.
_VECTOR_DIM = settings.embedding.dimension


# ``MappedAsDataclass`` makes ``Chunk`` its own row contract: it is the
# value type cbrkit exchanges (``Pgvector[UUID, Chunk]``), and the
# generated dataclass ``__init__`` is what statically verifies every
# write: a column rename, retype, or new required column breaks every
# ``Chunk(...)`` call site at type-check time.  ``init=False`` columns
# are cbrkit-owned (``id`` generated key, ``embedding``, ``tsv``) and never
# passed by the host; ``kw_only`` frees us from default-ordering rules.
class Chunk(MappedAsDataclass, Base, kw_only=True):  # pyright: ignore[reportUnsafeMultipleInheritance]
    """Per-row chunk content + embedding + host metadata, one row each.

    The ``id`` PK and ``text`` column are the cbrkit query surface
    (``value_column="text"``); ``embedding`` (:class:`PGVECTOR`) and
    ``tsv`` (:class:`TSVECTOR`, Postgres-generated from ``text``) are
    the dense/sparse index targets cbrkit populates on write, so they
    are ``init=False`` and excluded from the row dump.  All columns are
    declared explicitly here so hivegent owns the schema end-to-end.
    cbrkit attaches to it via ``Pgvector(model=...)``.
    ``embedding``'s dimension is the embedding-model-derived
    :data:`_VECTOR_DIM`.  Always written by the upload pipeline together
    with their owning document; cascade-deleted with it.
    ``Pgvector.reconcile`` fits ``embedding`` and ``tsv`` to this declaration
    at boot and keeps the fingerprint of the vectors as the comment of ``embedding``.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "idx"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, init=False, default_factory=uuid4
    )
    text: Mapped[str]
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int]
    token_count: Mapped[int]
    start_index: Mapped[int]
    end_index: Mapped[int]
    start_line: Mapped[int]
    end_line: Mapped[int]
    embedding: Mapped[Any] = mapped_column(
        PGVECTOR(_VECTOR_DIM), nullable=False, init=False, default=None
    )
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR(),
        tsvector_computed("text", settings.embedding.text_search_config),
        nullable=False,
        init=False,
        default=None,
    )


class McpConfirmation(Base):
    """The nonce of an MCP confirmation the user answered, spent until it expires.

    Kept in the database so an answer runs its call once across restarts,
    and swept once expired, since the HMAC refuses it from then on.
    """

    __tablename__ = "mcp_confirmations"

    nonce: Mapped[str] = mapped_column(primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class ApplicationSettings(Timestamped, Base):
    """Operator-set toggles that affect the entire instance.

    Singleton row (``id = 1``), named after GitLab's table of the same role.  Each global switch is a
    column with a ``server_default`` so the row is fully usable whether
    it predates the column or has never been written at all.  Readers
    treat an absent row as all-defaults.  Add future instance-wide
    toggles here instead of creating new singleton tables.
    """

    __tablename__ = "application_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    maintenance_enabled: Mapped[bool] = mapped_column(
        default=False, server_default=sa.false()
    )
