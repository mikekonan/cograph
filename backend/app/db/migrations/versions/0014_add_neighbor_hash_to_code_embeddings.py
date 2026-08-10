"""Add neighbor_hash column to code_embeddings.

neighbor_hash captures a short digest of a node's callers/callees qualified
names so the embedder can detect when the graph neighbourhood changes even
if the node's own content (content_hash) is unchanged.  An empty string
default is safe — existing rows will be treated as "neighbourhood unknown"
and will be re-embedded on the next sync once the embedder starts writing
the new column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_neighbor_hash"
down_revision = "0013_add_chunk_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "code_embeddings",
        sa.Column(
            "neighbor_hash",
            sa.Text,
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("code_embeddings", "neighbor_hash")
