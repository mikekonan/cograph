"""Add tsvector columns + GIN indexes for BM25 lexical search.

Adds a generated ``content_tsv`` tsvector column to three tables so that
full-text search (BM25-style scoring via ``ts_rank``) works across code
nodes and document chunks without a separate index pipeline.

code_nodes — weighted over four source fields:
  A (highest) qualified_name  → symbol name dominates scoring
  B             signature      → type info
  C             doc_comment    → docstring / JSDoc
  D (lowest)    content        → raw source code

repo_document_chunks / bank_document_chunks — content only.

All three GIN indexes are created CONCURRENTLY (inside autocommit_block)
to avoid blocking reads on large tables during the migration.

NOTE on partial-failure recovery
================================
This migration adds GENERATED ALWAYS AS STORED columns transactionally and creates
GIN indexes via CREATE INDEX CONCURRENTLY (inside `op.get_context().autocommit_block()`).
If a CONCURRENT index build fails mid-way (rare, but possible on very large tables
or concurrent DDL), Alembic will mark the revision as applied because the columns
were created in the prior transactional step.

To recover:
1. Check pg_indexes: SELECT indexname FROM pg_indexes WHERE tablename IN
   ('code_nodes', 'repo_document_chunks', 'bank_document_chunks');
2. If `idx_*_tsv` is missing, drop any partial INVALID index:
   SELECT 'DROP INDEX CONCURRENTLY ' || indexname FROM pg_index
   JOIN pg_class ON pg_class.oid = pg_index.indexrelid
   WHERE NOT indisvalid AND relname LIKE 'idx_%_tsv';
3. Recreate manually with the same DDL (see this migration's body).

Future migrations adding GIN indexes should be split into a columns-only migration
and an indexes-only migration to make recovery automatic via Alembic.
"""
from __future__ import annotations

from alembic import op

revision = "0015_add_bm25_tsvector_columns"
down_revision = "0014_neighbor_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # code_nodes: weighted tsvector (qualified_name=A, signature=B, doc_comment=C, content=D)
    op.execute(
        "ALTER TABLE code_nodes ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS ("
        "  setweight(to_tsvector('english', coalesce(qualified_name, '')), 'A') ||"
        "  setweight(to_tsvector('english', coalesce(signature,      '')), 'B') ||"
        "  setweight(to_tsvector('english', coalesce(doc_comment,    '')), 'C') ||"
        "  setweight(to_tsvector('english', coalesce(content,        '')), 'D')"
        ") STORED"
    )

    # repo_document_chunks: content only
    op.execute(
        "ALTER TABLE repo_document_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED"
    )

    # bank_document_chunks: content only
    op.execute(
        "ALTER TABLE bank_document_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED"
    )

    # GIN indexes — CONCURRENTLY requires autocommit (no surrounding transaction).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_tsv "
            "ON code_nodes USING gin (content_tsv)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_repo_doc_chunks_tsv "
            "ON repo_document_chunks USING gin (content_tsv)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bank_chunks_tsv "
            "ON bank_document_chunks USING gin (content_tsv)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_bank_chunks_tsv")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_repo_doc_chunks_tsv")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_code_nodes_tsv")

    op.drop_column("bank_document_chunks", "content_tsv")
    op.drop_column("repo_document_chunks", "content_tsv")
    op.drop_column("code_nodes", "content_tsv")
