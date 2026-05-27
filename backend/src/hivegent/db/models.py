"""SQLAlchemy 2.0 schema for Hivegent.

Single module — small enough to read top-to-bottom.  Stays dialect-neutral
so swapping ``sqlite+aiosqlite`` for ``postgresql+psycopg`` is a config
change.  Repositories convert these ORM rows to Pydantic models at their
public surface; nothing outside :mod:`hivegent.db` sees an ORM object.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from nanoid import generate as _nanoid
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, DateTime

__all__ = [
    "Base",
    "Chunk",
    "Conversation",
    "Document",
    "EntryKind",
    "GeneratedBy",
    "Group",
    "GroupMember",
    "IndexState",
    "Memory",
    "Message",
    "MessageKind",
    "Origin",
    "Permission",
    "Timestamped",
    "Token",
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
        dict[str, Any]: JSON,
        list[str]: JSON,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _nid() -> str:
    return _nanoid(size=21)


class Timestamped:
    """Mixin adding ``created_at`` / ``updated_at`` columns.

    ``updated_at`` is bumped automatically on every UPDATE via ``onupdate``;
    callers only need to assign it explicitly when they want to mark a row
    as modified without changing any of its other columns (e.g. a parent
    whose only "change" is an inserted child).
    """

    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


def _enum(t: type[enum.StrEnum]) -> Enum:
    """Portable enum column — VARCHAR + CHECK on both SQLite and Postgres."""
    return Enum(t, native_enum=False, validate_strings=True)


# ─── Enums ─────────────────────────────────────────────────────────────


class Permission(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class EntryKind(enum.StrEnum):
    USER_MARKDOWN = "user_markdown"
    IMAGE = "image"
    CONVERTIBLE = "convertible"
    BINARY_STUB = "binary_stub"


class Origin(enum.StrEnum):
    UPLOAD = "upload"
    COLLECTION = "collection"
    EXTRACTED = "extracted"


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
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str | None]
    display_name: Mapped[str | None]

    tokens: Mapped[list[Token]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memory: Mapped[Memory | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="owner_user",
        cascade="all, delete-orphan",
        foreign_keys="Document.owner_user_id",
    )
    memberships: Mapped[list[GroupMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Group(Timestamped, Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(primary_key=True)
    display_name: Mapped[str | None]

    members: Mapped[list[GroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="owner_group",
        cascade="all, delete-orphan",
        foreign_keys="Document.owner_group_id",
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[str] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    permission: Mapped[Permission] = mapped_column(_enum(Permission))

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


# ─── Personal access tokens ────────────────────────────────────────────


class Token(Timestamped, Base):
    __tablename__ = "tokens"

    id: Mapped[str] = mapped_column(primary_key=True, default=_nid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    hash: Mapped[str]
    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="tokens")


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
        ForeignKey("conversations.id", ondelete="SET NULL")
    )

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.idx",
    )


class Message(Timestamped, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[MessageKind] = mapped_column(_enum(MessageKind))
    payload: Mapped[dict[str, Any]]

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ─── Documents & chunks ────────────────────────────────────────────────


class Document(Timestamped, Base):
    """A logical stem entry inside a user or group casebase.

    Path columns hold only the irreducible bits.  ``description_path``,
    ``original_path`` and ``assets_dir`` are derived from ``stem_path``,
    ``original_ext`` and ``has_assets`` at the repository layer.
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
    original_ext: Mapped[str | None]
    has_assets: Mapped[bool] = mapped_column(default=False)

    entry_kind: Mapped[EntryKind] = mapped_column(_enum(EntryKind))
    origin: Mapped[Origin] = mapped_column(_enum(Origin))
    generated_by: Mapped[GeneratedBy] = mapped_column(_enum(GeneratedBy))
    mime: Mapped[str | None]

    pipeline: Mapped[str]
    content_sha256: Mapped[str | None]

    owner_user: Mapped[User | None] = relationship(
        back_populates="documents", foreign_keys=[owner_user_id]
    )
    owner_group: Mapped[Group | None] = relationship(
        back_populates="documents", foreign_keys=[owner_group_id]
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Chunk.idx",
    )


class Chunk(Base):
    __tablename__ = "chunks"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str]
    token_count: Mapped[int]
    start_index: Mapped[int]
    end_index: Mapped[int]
    start_line: Mapped[int]
    end_line: Mapped[int]

    document: Mapped[Document] = relationship(back_populates="chunks")


# ─── LanceDB fingerprint ───────────────────────────────────────────────


class IndexState(Base):
    """Global embedding fingerprint for the LanceDB index.

    Singleton row (``id = 1``).  The application has one embedding model,
    so one fingerprint covers the whole index.  A mismatch wipes the
    LanceDB directory; the index then rebuilds from the source-of-truth
    ``chunks`` table on the next sync.
    """

    __tablename__ = "index_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    embedding_provider: Mapped[str]
    embedding_model: Mapped[str]
    fingerprint_set_at: Mapped[datetime] = mapped_column(default=_now)
