"""SQLAlchemy 2.0 schema for Hivegent.

Single module — small enough to read top-to-bottom.  Repositories convert
these ORM rows to Pydantic models at their public surface; nothing
outside :mod:`hivegent.db` sees an ORM object.

The PostgreSQL/pgvector ``chunks`` table holds chunk metadata, text,
and vectors in one normalised row.  cbrkit reads it via
:class:`pgvector_async(model=...)` for search and re-embedding;
hivegent writes to it via plain SQLAlchemy in the same session that
inserts the parent ``documents`` row, so there is no ordering coupling
between chunk metadata and vectors.
"""

import enum
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from cbrkit.indexable import PGVECTOR, TSVECTOR, tsvector_computed
from nanoid import generate as _nanoid
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)
from sqlalchemy.types import DateTime

from ..config import settings

__all__ = [
    "Base",
    "Chunk",
    "Conversation",
    "Document",
    "EntryKind",
    "GeneratedBy",
    "ApplicationSettings",
    "Group",
    "IndexState",
    "Memory",
    "Message",
    "MessageKind",
    "Origin",
    "Timestamped",
    "User",
]


# ─── Base ──────────────────────────────────────────────────────────────


_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Typed declarative base with portable annotation map."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _nid() -> str:
    return _nanoid(size=21)


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
    ``VARCHAR + CHECK`` — values live in one place (the type) rather than
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
    CONVERTIBLE = "convertible"
    BINARY_STUB = "binary_stub"


class Origin(enum.StrEnum):
    UPLOAD = "upload"
    COLLECTION = "collection"
    EXTRACTED = "extracted"
    # Discovered on disk by the reconciler with no prior row (hand-dropped
    # files, and the future read-write shell tool's fold-back).
    IMPORTED = "imported"


class GeneratedBy(enum.StrEnum):
    USER = "user"
    CONVERTER = "converter"
    VISION = "vision"
    STUB = "stub"


class MessageKind(enum.StrEnum):
    REQUEST = "request"
    RESPONSE = "response"


# ─── Identity ──────────────────────────────────────────────────────────


class User(Timestamped, Base):
    """A user that has left a footprint in the local database.

    Identity attributes (email, display name) and group membership are
    not stored: they live solely in the OIDC token and are reconstructed
    per request.  A row here is just the anchor that owns conversations,
    documents, and memory.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)

    memory: Mapped["Memory | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="owner_user",
        cascade="all, delete-orphan",
        foreign_keys="Document.owner_user_id",
    )


class Group(Timestamped, Base):
    """A group that owns shared documents.

    Like :class:`User`, membership is an OIDC concern and is never
    persisted; a row here only anchors group-owned documents.
    """

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(primary_key=True)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="owner_group",
        cascade="all, delete-orphan",
        foreign_keys="Document.owner_group_id",
    )


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
    __tablename__ = "conversations"
    __table_args__ = (Index(None, "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=_nid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None]
    compacted_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.idx",
    )


class Message(Base):
    """An immutable, append-only turn in a conversation.

    Messages are never updated in place (new turns are inserted at the
    next ``idx``), so the row carries only ``created_at`` — no
    ``updated_at`` — and that timestamp also has a ``server_default``.
    """

    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[MessageKind] = mapped_column(_enum(MessageKind))
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(
        default=_now, server_default=sa.func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ─── Documents & chunks ────────────────────────────────────────────────


class Document(Timestamped, Base):
    """A logical stem entry inside a user or group casebase.

    Path columns hold only the irreducible bits.  ``description_path``,
    ``original_path`` and ``assets_dir`` are derived from ``stem_path``,
    ``original_suffix`` and ``has_assets`` at the repository layer.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (owner_group_id IS NOT NULL)",
            name="single_owner",
        ),
        UniqueConstraint("owner_user_id", "stem_path"),
        UniqueConstraint("owner_group_id", "stem_path"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=_nid)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    owner_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE")
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
    mime: Mapped[str | None]

    pipeline: Mapped[str]
    content_digest: Mapped[str | None]
    # ``(mtime_ns, size)`` of the indexed markdown: a stat fast-path that lets
    # the reconciler skip re-reading a description whose stat is unchanged.
    content_mtime_ns: Mapped[int | None] = mapped_column(sa.BigInteger())
    content_size: Mapped[int | None] = mapped_column(sa.BigInteger())

    owner_user: Mapped[User | None] = relationship(
        back_populates="documents", foreign_keys=[owner_user_id]
    )
    owner_group: Mapped[Group | None] = relationship(
        back_populates="documents", foreign_keys=[owner_group_id]
    )


# ─── Chunks (metadata + text + vector, one row each) ──────────────────


# Schema dim is the plain configured ``HIVEGENT_EMBEDDING__DIMENSION``
# (never a model probe), so importing this module for Alembic
# autogenerate, the server, or unit tests stays offline.  A
# schema-vs-runtime mismatch is caught either by autogenerate (drift)
# or by the boot-time guard in the FastAPI lifespan.
_VECTOR_DIM = settings.embedding.dimension


# ``MappedAsDataclass`` makes ``Chunk`` its own row contract: it is the
# value type cbrkit exchanges (``pgvector_async[str, Chunk]``), and the
# generated dataclass ``__init__`` is what statically verifies every
# write — a column rename, retype, or new required column breaks every
# ``Chunk(...)`` call site at type-check time.  ``init=False`` columns
# are cbrkit-owned (``id`` nanoid key, ``embedding``, ``tsv``) and never
# passed by the host; ``kw_only`` frees us from default-ordering rules.
class Chunk(MappedAsDataclass, Base, kw_only=True):  # pyright: ignore[reportUnsafeMultipleInheritance]
    """Per-row chunk content + embedding + host metadata, one row each.

    The ``id`` PK and ``text`` column are the cbrkit query surface
    (``value_column="text"``); ``embedding`` (:class:`PGVECTOR`) and
    ``tsv`` (:class:`TSVECTOR`, Postgres-generated from ``text``) are
    the dense/sparse index targets cbrkit populates on write, so they
    are ``init=False`` and excluded from the row dump.  All columns are
    declared explicitly here so hivegent owns the schema end-to-end —
    cbrkit attaches to it via ``pgvector_async(model=...)``.
    ``embedding``'s dimension is the embedding-model-derived
    :data:`_VECTOR_DIM`.  Always written by the upload pipeline together
    with their owning document; cascade-deleted with it.
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

    id: Mapped[str] = mapped_column(primary_key=True, init=False, default_factory=_nid)
    text: Mapped[str]
    document_id: Mapped[str] = mapped_column(
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


class IndexState(Base):
    """Global embedding fingerprint for the vector index.

    Singleton row (``id = 1``).  The application has one embedding model,
    so one fingerprint covers the whole index.  A mismatch drives an
    in-place ``reembed_all`` against ``chunks`` — no truncate, no data
    loss.
    """

    __tablename__ = "index_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    embedding_provider: Mapped[str]
    embedding_model: Mapped[str]
    fingerprint_set_at: Mapped[datetime] = mapped_column(
        default=_now, onupdate=_now, server_default=sa.func.now()
    )


class ApplicationSettings(Timestamped, Base):
    """Operator-set toggles that affect the entire instance.

    Singleton row (``id = 1``), mirroring :class:`IndexState` and named
    after GitLab's table of the same role.  Each global switch is a
    column with a ``server_default`` so the row is fully usable whether
    it predates the column or has never been written at all — readers
    treat an absent row as all-defaults.  Add future instance-wide
    toggles here instead of creating new singleton tables.
    """

    __tablename__ = "application_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    maintenance_enabled: Mapped[bool] = mapped_column(
        default=False, server_default=sa.false()
    )
