"""add application_settings singleton for global toggles

New ``application_settings`` table (singleton row, ``id = 1``) holding
operator-set switches that affect the entire instance — currently only
``maintenance_enabled``.  Every toggle column carries a server default
so an absent or pre-existing row always reads as all-defaults.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-06-11 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "maintenance_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_NOW,
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_application_settings_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_settings")),
    )


def downgrade() -> None:
    op.drop_table("application_settings")
