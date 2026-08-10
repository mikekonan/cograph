"""Add embedding + model columns to repo_document_chunks and bank_document_chunks.

Physical blocker for phase 7 (multi-store RAG): both chunk tables need a
pgvector ``embedding`` column and a ``model`` label so the RAG indexer can
populate them per document and run HNSW cosine-distance retrieval.

Columns added:
  - embedding  vector(1536) NULL   — pgvector; nullable until backfilled
  - model      TEXT NOT NULL DEFAULT ''  — embedder model identifier

Indexes:
  - HNSW cosine index per table, partial WHERE embedding IS NOT NULL.
  - Created CONCURRENTLY inside an autocommit_block so existing rows in
    production are not blocked during the build.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_add_chunk_embeddings"
down_revision = "0012_code_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename enum value: generate_docs → index_repo_docs (pipeline step rename).
    op.execute("UPDATE sync_jobs SET step = 'index_repo_docs' WHERE step = 'generate_docs'")

    # pgvector must exist (0012 creates it; repeat is idempotent).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- repo_document_chunks ---
    op.add_column(
        "repo_document_chunks",
        sa.Column("embedding", sa.Text, nullable=True),  # retyped below
    )
    op.execute(
        "ALTER TABLE repo_document_chunks "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )
    op.add_column(
        "repo_document_chunks",
        sa.Column(
            "model",
            sa.Text,
            nullable=False,
            server_default="",
        ),
    )

    # --- bank_document_chunks ---
    op.add_column(
        "bank_document_chunks",
        sa.Column("embedding", sa.Text, nullable=True),
    )
    op.execute(
        "ALTER TABLE bank_document_chunks "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )
    op.add_column(
        "bank_document_chunks",
        sa.Column(
            "model",
            sa.Text,
            nullable=False,
            server_default="",
        ),
    )

    # CREATE INDEX CONCURRENTLY must run outside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_repo_doc_chunks_hnsw "
            "ON repo_document_chunks USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bank_chunks_hnsw "
            "ON bank_document_chunks USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_bank_chunks_hnsw")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_repo_doc_chunks_hnsw")

    op.drop_column("bank_document_chunks", "model")
    op.drop_column("bank_document_chunks", "embedding")
    op.drop_column("repo_document_chunks", "model")
    op.drop_column("repo_document_chunks", "embedding")

    # Reverse rename: index_repo_docs → generate_docs.
    op.execute("UPDATE sync_jobs SET step = 'generate_docs' WHERE step = 'index_repo_docs'")
