"""baseline schema

Revision ID: e5a1b2c3d4f5
Revises:
Create Date: 2026-05-28 11:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ENUM, JSONB, TSVECTOR


revision: str = "e5a1b2c3d4f5"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Dim of the default ``paraphrase-multilingual-MiniLM-L12-v2`` model.
# Operators running a different embedding model must generate a
# follow-up revision ALTERing ``chunks.embedding`` to the matching dim.
_VECTOR_DIM = 384

# ``create_type=False`` keeps SQLAlchemy from auto-emitting ``CREATE TYPE``
# while building the referencing tables; the enums are created once,
# idempotently, by the explicit loop in ``upgrade`` below.
_PERMISSION = ENUM("read", "write", name="permission", create_type=False)
_ENTRY_KIND = ENUM(
    "user_markdown", "image", "convertible", "binary_stub",
    name="entrykind", create_type=False,
)
_ORIGIN = ENUM("upload", "collection", "extracted", name="origin", create_type=False)
_GENERATED_BY = ENUM(
    "user", "converter", "vision", "stub", name="generatedby", create_type=False
)
_MESSAGE_KIND = ENUM("request", "response", name="messagekind", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    bind = op.get_bind()
    for enum_type in (
        _PERMISSION,
        _ENTRY_KIND,
        _ORIGIN,
        _GENERATED_BY,
        _MESSAGE_KIND,
    ):
        enum_type.create(bind)

    op.create_table(
        "groups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
    )
    op.create_table(
        "index_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("embedding_provider", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("fingerprint_set_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_index_state_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_state")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("compacted_from_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["compacted_from_id"],
            ["conversations.id"],
            name=op.f("fk_conversations_compacted_from_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        op.f("ix_conversations_user_id_updated_at"),
        "conversations",
        ["user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.Column("owner_group_id", sa.String(), nullable=True),
        sa.Column("stem_path", sa.String(), nullable=False),
        sa.Column("original_ext", sa.String(), nullable=True),
        sa.Column("has_assets", sa.Boolean(), nullable=False),
        sa.Column("entry_kind", _ENTRY_KIND, nullable=False),
        sa.Column("origin", _ORIGIN, nullable=False),
        sa.Column("generated_by", _GENERATED_BY, nullable=False),
        sa.Column("mime", sa.String(), nullable=True),
        sa.Column("pipeline", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(owner_user_id IS NOT NULL) <> (owner_group_id IS NOT NULL)",
            name=op.f("ck_documents_single_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_group_id"],
            ["groups.id"],
            name=op.f("fk_documents_owner_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_documents_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "owner_group_id",
            "stem_path",
            name=op.f("uq_documents_owner_group_id_stem_path"),
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "stem_path",
            name=op.f("uq_documents_owner_user_id_stem_path"),
        ),
    )
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("permission", _PERMISSION, nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_members_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_group_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_group_members")),
    )
    op.create_index(
        op.f("ix_group_members_user_id"), "group_members", ["user_id"], unique=False
    )

    op.create_table(
        "memory",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memory_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_memory")),
    )
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", _MESSAGE_KIND, nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "idx", name=op.f("pk_messages")),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_VECTOR_DIM), nullable=False),
        sa.Column(
            "tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("start_index", sa.Integer(), nullable=False),
        sa.Column("end_index", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_id", "idx", name=op.f("uq_chunks_document_id_idx")
        ),
    )
    op.create_index(
        op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False
    )
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_tsv",
        "chunks",
        ["tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("messages")
    op.drop_table("memory")
    op.drop_index(op.f("ix_group_members_user_id"), table_name="group_members")
    op.drop_table("group_members")
    op.drop_table("documents")
    op.drop_index(
        op.f("ix_conversations_user_id_updated_at"), table_name="conversations"
    )
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_table("index_state")
    op.drop_table("groups")

    bind = op.get_bind()
    for enum_type in (
        _MESSAGE_KIND,
        _GENERATED_BY,
        _ORIGIN,
        _ENTRY_KIND,
        _PERMISSION,
    ):
        enum_type.drop(bind)

    op.execute("DROP EXTENSION IF EXISTS vector")
