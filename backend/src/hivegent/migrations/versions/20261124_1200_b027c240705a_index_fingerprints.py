"""index fingerprints

Every derived row records what made it, so the reconcile at boot finds what
the configuration no longer makes.  A document stamps the fingerprint of the
chunker that made its chunks in ``chunker`` next to the digest of its text,
and rows without one are chunked again at the next boot, since what made them
is unknown.  cbrkit keeps the fingerprint of the stored vectors as the comment
of ``chunks.embedding`` instead of the ``index_state`` row, whose values carry
over in the format ``reconcile_index_state`` writes, so no vector is embedded
again.

Revision ID: b027c240705a
Revises: b04af7293fa8
Create Date: 2026-11-24 12:00:00.000000+00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b027c240705a"
down_revision: str | Sequence[str] | None = "b04af7293fa8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("chunker", sa.String(), nullable=True))
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT embedding_provider, embedding_model, embedding_document_prefix "
                "FROM index_state"
            )
        )
        .first()
    )

    if row is not None:
        provider, model, document_prefix = row
        fingerprint = json.dumps(
            {"provider": provider, "model": model, "document_prefix": document_prefix},
            sort_keys=True,
        )
        op.alter_column("chunks", "embedding", comment=fingerprint)

    op.drop_table("index_state")


def downgrade() -> None:
    op.create_table(
        "index_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("embedding_provider", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column(
            "embedding_document_prefix",
            sa.String(),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "fingerprint_set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_index_state_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_state")),
    )
    op.alter_column("chunks", "embedding", comment=None)
    op.drop_column("documents", "chunker")
