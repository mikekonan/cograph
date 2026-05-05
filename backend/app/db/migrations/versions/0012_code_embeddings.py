"""Add code_embeddings table for graph-enriched node vectors.

Stores one embedding row per code_node.  content_hash mirrors the source
node so the embedder can skip unchanged nodes on incremental runs.

HNSW index uses cosine distance, matching the intended RAG retrieval query.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_code_embeddings"
down_revision = "0011_repo_enrichment_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension is available (idempotent).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "code_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "code_node_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "embedding",
            sa.Text,  # placeholder; real column type set via raw SQL below
            nullable=True,
        ),
        sa.Column(
            "model",
            sa.Text,
            nullable=False,
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["code_node_id"],
            ["code_nodes.id"],
            ondelete="CASCADE",
            name="fk_code_embeddings_code_node_id",
        ),
        sa.UniqueConstraint("code_node_id", name="uq_code_embeddings_node"),
    )

    # Alter embedding column to the native pgvector type so HNSW is possible.
    op.execute("ALTER TABLE code_embeddings ALTER COLUMN embedding TYPE vector(1536) USING NULL")

    # HNSW index with cosine distance — used by the RAG kNN query.
    op.execute(
        "CREATE INDEX idx_code_embeddings_hnsw "
        "ON code_embeddings USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_index(
        "ix_code_embeddings_code_node_id",
        "code_embeddings",
        ["code_node_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_code_embeddings_hnsw", table_name="code_embeddings")
    op.drop_index("ix_code_embeddings_code_node_id", table_name="code_embeddings")
    op.drop_table("code_embeddings")
