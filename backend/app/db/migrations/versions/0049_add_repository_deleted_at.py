"""Add repositories.deleted_at for soft-delete + cascade worker.

Revision ID: 0049_add_repository_deleted_at
Revises: 0048_add_idp_auto_link

A repository delete used to run the FK CASCADE synchronously and could
take minutes on big repos because HNSW vector-index maintenance happens
one row at a time on the embedding tables. The DELETE handler now flips
`status -> DELETING` and sets `deleted_at = now()`, returns 204
immediately, and an arq worker drains the cascade in chunked
transactions in the background. Read paths gate on
`deleted_at IS NULL` so the row vanishes from users' view from the
instant the click lands.

A partial index on `deleted_at IS NULL` keeps every read path's
filter fast — the live-row set is what gets scanned for every list
and slug lookup, while the (smaller) deleted set is left out
entirely until the purge worker drops the row for good.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_add_repository_deleted_at"
down_revision = "0048_add_idp_auto_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_repositories_alive",
        "repositories",
        ["id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_repositories_alive", table_name="repositories")
    op.drop_column("repositories", "deleted_at")
