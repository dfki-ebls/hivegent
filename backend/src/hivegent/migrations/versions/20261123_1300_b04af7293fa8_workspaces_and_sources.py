"""workspaces and sources

Every workspace becomes a ``workspaces`` row keyed by its scope's key
(``user:<id>``, ``group:<id>``, ``source:<id>``, ``global``), which names the
user, group, or source it belongs to, and none for the global one, and goes
with that parent.  A document names its workspace by that key alone, unique by
its stem there, instead of one of two owner columns, so the global workspace is
an ordinary row and deleting a user or group takes its documents along.  The
upgrade creates a row for every owner a document names.

The external sources the operator configures keep their sync state: one
``sources`` row each with its cursor, the sync an admin ``requested`` with a
``request_seq`` counting the requests, so a sync clears only the one it
claimed, and the ``source_items`` mapping each file of a source to its path in the mirror, its
version, and whether its deletion is ``held``.  A source's mirror is a
workspace keyed ``source:<id>``, and the ``synced`` origin marks what a sync
mirrored.

``index_state`` adds the document prefix to the embedding fingerprint, since
it changes every stored vector, and existing vectors were embedded without one.
``users.preferences`` keeps the choices a user made for their runs.
``mcp_confirmations`` keeps the nonce of every answered MCP confirmation until
it expires, so an answer runs its call once across restarts.

The downgrade drops the global and source workspaces with their documents,
which the old owner columns cannot express, and every source row, which a full
sync rebuilds.  PostgreSQL cannot drop the ``synced`` label, so it stays.

Revision ID: b04af7293fa8
Revises: 65f6705faca2
Create Date: 2026-11-23 13:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b04af7293fa8"
down_revision: str | Sequence[str] | None = "65f6705faca2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = "coalesce('user:' || owner_user_id, 'group:' || owner_group_id)"


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested", sa.String(), nullable=True),
        sa.Column("request_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
    )
    op.create_table(
        "source_items",
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("remote_id", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("held", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_items_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_id", "remote_id", name=op.f("pk_source_items")),
        sa.UniqueConstraint(
            "source_id", "path", name=op.f("uq_source_items_source_id_path")
        ),
    )
    op.create_table(
        "workspaces",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("group_id", sa.String(), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(user_id, group_id, source_id) <= 1 AND key = "
            "coalesce('user:' || user_id, 'group:' || group_id, "
            "'source:' || source_id, 'global')",
            name=op.f("ck_workspaces_parent"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_workspaces_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_workspaces_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspaces_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_workspaces")),
    )
    op.execute(
        f"INSERT INTO workspaces (key, user_id, group_id) "
        f"SELECT DISTINCT {_KEY}, owner_user_id, owner_group_id FROM documents"
    )
    op.add_column("documents", sa.Column("workspace_key", sa.String(), nullable=True))
    op.execute(f"UPDATE documents SET workspace_key = {_KEY}")
    op.alter_column("documents", "workspace_key", nullable=False)
    op.create_foreign_key(
        op.f("fk_documents_workspace_key_workspaces"),
        "documents",
        "workspaces",
        ["workspace_key"],
        ["key"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_documents_workspace_key_stem_path"),
        "documents",
        ["workspace_key", "stem_path"],
    )
    op.drop_constraint(op.f("ck_documents_single_owner"), "documents", type_="check")
    op.drop_constraint(
        op.f("uq_documents_owner_user_id_stem_path"), "documents", type_="unique"
    )
    op.drop_constraint(
        op.f("uq_documents_owner_group_id_stem_path"), "documents", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_documents_owner_user_id_users"), "documents", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_documents_owner_group_id_groups"), "documents", type_="foreignkey"
    )
    op.drop_column("documents", "owner_user_id")
    op.drop_column("documents", "owner_group_id")
    op.add_column(
        "index_state",
        sa.Column(
            "embedding_document_prefix", sa.String(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_table(
        "mcp_confirmations",
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("nonce", name=op.f("pk_mcp_confirmations")),
    )
    op.create_index(
        op.f("ix_mcp_confirmations_expires_at"), "mcp_confirmations", ["expires_at"]
    )
    # ``ADD VALUE`` is allowed inside Alembic's transaction on PostgreSQL 12+
    # as long as the new label is not used in the same transaction.
    op.execute("ALTER TYPE origin ADD VALUE IF NOT EXISTS 'synced'")


def downgrade() -> None:
    op.drop_table("mcp_confirmations")
    op.drop_column("users", "preferences")
    op.drop_column("index_state", "embedding_document_prefix")
    op.execute("UPDATE documents SET origin = 'imported' WHERE origin = 'synced'")
    op.execute("DELETE FROM workspaces WHERE key = 'global' OR source_id IS NOT NULL")
    op.add_column("documents", sa.Column("owner_user_id", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("owner_group_id", sa.String(), nullable=True))
    op.execute(
        "UPDATE documents SET owner_user_id = workspaces.user_id, "
        "owner_group_id = workspaces.group_id "
        "FROM workspaces WHERE workspaces.key = documents.workspace_key"
    )
    op.create_foreign_key(
        op.f("fk_documents_owner_group_id_groups"),
        "documents",
        "groups",
        ["owner_group_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_documents_owner_user_id_users"),
        "documents",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_documents_owner_group_id_stem_path"),
        "documents",
        ["owner_group_id", "stem_path"],
    )
    op.create_unique_constraint(
        op.f("uq_documents_owner_user_id_stem_path"),
        "documents",
        ["owner_user_id", "stem_path"],
    )
    op.create_check_constraint(
        op.f("ck_documents_single_owner"),
        "documents",
        "(owner_user_id IS NOT NULL) <> (owner_group_id IS NOT NULL)",
    )
    op.drop_constraint(
        op.f("uq_documents_workspace_key_stem_path"), "documents", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_documents_workspace_key_workspaces"), "documents", type_="foreignkey"
    )
    op.drop_column("documents", "workspace_key")
    op.drop_table("workspaces")
    op.drop_table("source_items")
    op.drop_table("sources")
