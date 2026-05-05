"""Phase 7d — pg_trgm extension + simple-tokenizer tsvector for code identifiers.

Two additions on top of migration 0015:

1. ``pg_trgm`` extension + GIN trigram index on ``code_nodes.qualified_name``.
   Enables ``SymbolLookup`` (fuzzy symbol matching) — finds ``foo_bar_baz`` when
   the user types ``foobarbaz``.  Pure BM25 misses these because tokenisation
   splits on word boundaries.

2. ``content_tsv_simple`` GENERATED STORED column on ``code_nodes`` using the
   ``'simple'`` text-search config.  The ``english`` config (added in 0015)
   stems and strips stopwords — fine for prose, harmful for code identifiers
   (``HttpError`` → ``httperror`` is fine, but ``parse_request`` → ``pars`` +
   ``request`` loses precision; reserved words like ``do``/``in``/``is`` are
   dropped entirely).  ``simple`` lowercases without stemming, preserving the
   identifier as-is.  ``LexicalRetriever`` uses ``content_tsv_simple`` for the
   ``code`` store and ``content_tsv`` for ``repo_docs``/``banks``.

NOTE on partial-failure recovery (same as 0015)
================================================
GIN indexes are created via ``CREATE INDEX CONCURRENTLY`` inside an
``autocommit_block``.  If a CONCURRENT build fails mid-way Alembic still
marks the revision as applied because the column was added in the prior
transactional step.  Recovery: drop any INVALID index (see 0015's docstring
for the recipe) and recreate manually with the DDL below.
"""
from __future__ import annotations

from alembic import op


revision = "0018_trgm_and_simple_tsv"
down_revision = "0017_ast_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Trigram extension — IF NOT EXISTS so it's safe in environments where
    #    a sysadmin pre-installed it via a separate migration / db init script.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 2. simple-tokenizer tsvector on code_nodes (mirrors 0015's weighted scheme).
    op.execute(
        "ALTER TABLE code_nodes ADD COLUMN content_tsv_simple tsvector "
        "GENERATED ALWAYS AS ("
        "  setweight(to_tsvector('simple', coalesce(qualified_name, '')), 'A') ||"
        "  setweight(to_tsvector('simple', coalesce(signature,      '')), 'B') ||"
        "  setweight(to_tsvector('simple', coalesce(doc_comment,    '')), 'C') ||"
        "  setweight(to_tsvector('simple', coalesce(content,        '')), 'D')"
        ") STORED"
    )

    # 3. CONCURRENTLY-built GIN indexes — autocommit required.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_tsv_simple "
            "ON code_nodes USING gin (content_tsv_simple)"
        )
        # gin_trgm_ops on qualified_name powers SymbolLookup similarity queries.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_qualname_trgm "
            "ON code_nodes USING gin (qualified_name gin_trgm_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_code_nodes_qualname_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_code_nodes_tsv_simple")

    op.drop_column("code_nodes", "content_tsv_simple")
    # Intentionally do NOT drop the pg_trgm extension on downgrade — it's a
    # cluster-wide resource and another database / extension may rely on it.
