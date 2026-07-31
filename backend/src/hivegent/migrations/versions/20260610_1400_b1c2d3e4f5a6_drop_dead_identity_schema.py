"""drop dead identity/membership schema

Removes columns and a table that the application never wrote to: user
identity (``users.email``, ``users.display_name``), group identity
(``groups.display_name``), and the ``group_members`` table with its
``permission`` enum.  Group membership and user identity live solely in
the OIDC token and are reconstructed per request, never persisted.

Safe by construction: every object dropped here was always empty/NULL,
so no data is lost.  Idempotent across deployments — a freshly created
database runs the baseline (which still creates these objects) and then
this revision drops them, converging to the same schema as an existing
database that only runs this revision.

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-06-10 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PERMISSION = ENUM("read", "write", name="permission", create_type=False)


def upgrade() -> None:
    op.drop_table("group_members")
    _PERMISSION.drop(op.get_bind(), checkfirst=True)
    op.drop_constraint(op.f("uq_users_email"), "users", type_="unique")
    op.drop_column("users", "email")
    op.drop_column("users", "display_name")
    op.drop_column("groups", "display_name")


def downgrade() -> None:
    bind = op.get_bind()
    _PERMISSION.create(bind, checkfirst=True)
    op.add_column("groups", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.create_unique_constraint(op.f("uq_users_email"), "users", ["email"])
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
