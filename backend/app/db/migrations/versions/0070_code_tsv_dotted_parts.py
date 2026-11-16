"""code_nodes.content_tsv_simple also holds the parts of dotted names and paths.

Revision ID: 0070_code_tsv_dotted_parts
Revises: 0069_repo_document_kind_lines

Postgres' default text-search parser keeps `obj.Name`, `self.store.GetShelf`
and every dotted `qualified_name` as one `host` token, and `a/b/c.go` as one
`file` token. So a name that the code only ever writes after a dot was not
in the index on its own, and a lexical search for it found nothing.

The column now holds every token whole, as before, plus the same text with
`.` and `/` turned into spaces, so each part is a token too. The whole
tokens stay: they rank natural-language queries, and the parts alone made
that ranking worse. `LexicalRetriever` matches a dotted query term against
either form.

Also drops `code_nodes.content_tsv` (the `english` column from 0015) and its
GIN index: no query reads it, and every write paid for it. The
`content_tsv` columns of the document tables stay.

LOCKING: adding a GENERATED STORED column rewrites `code_nodes` under an
ACCESS EXCLUSIVE lock, so reads and writes of the table wait until the
rewrite ends. Deploy when no sync runs. Until the CONCURRENT index build
that follows finishes, lexical code search scans the table.

NOTE on partial-failure recovery (same as 0018)
================================================
The GIN index is created via ``CREATE INDEX CONCURRENTLY`` inside an
``autocommit_block``.  If the CONCURRENT build fails mid-way Alembic still
marks the revision as applied because the column was added in the prior
transactional step.  Recovery: drop any INVALID index (see 0015's docstring
for the recipe) and recreate manually with the DDL below.
"""
from __future__ import annotations

from alembic import op

revision = "0070_code_tsv_dotted_parts"
down_revision = "0069_repo_document_kind_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One ALTER, one table rewrite. Dropping a column drops its index.
    op.execute(
        "ALTER TABLE code_nodes"
        "  DROP COLUMN content_tsv,"
        "  DROP COLUMN content_tsv_simple,"
        "  ADD COLUMN content_tsv_simple tsvector GENERATED ALWAYS AS ("
        "    setweight(to_tsvector('simple', coalesce(qualified_name, '')), 'A') ||"
        "    setweight(to_tsvector('simple', coalesce(signature,      '')), 'B') ||"
        "    setweight(to_tsvector('simple', coalesce(doc_comment,    '')), 'C') ||"
        "    setweight(to_tsvector('simple', coalesce(content,        '')), 'D') ||"
        "    setweight(to_tsvector('simple', translate(coalesce(qualified_name, ''), './', '  ')), 'A') ||"
        "    setweight(to_tsvector('simple', translate(coalesce(signature,      ''), './', '  ')), 'B') ||"
        "    setweight(to_tsvector('simple', translate(coalesce(doc_comment,    ''), './', '  ')), 'C') ||"
        "    setweight(to_tsvector('simple', translate(coalesce(content,        ''), './', '  ')), 'D')"
        "  ) STORED"
    )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_tsv_simple "
            "ON code_nodes USING gin (content_tsv_simple)"
        )


def downgrade() -> None:
    # The columns of 0015 and 0018 as they were.
    op.execute(
        "ALTER TABLE code_nodes"
        "  DROP COLUMN content_tsv_simple,"
        "  ADD COLUMN content_tsv tsvector GENERATED ALWAYS AS ("
        "    setweight(to_tsvector('english', coalesce(qualified_name, '')), 'A') ||"
        "    setweight(to_tsvector('english', coalesce(signature,      '')), 'B') ||"
        "    setweight(to_tsvector('english', coalesce(doc_comment,    '')), 'C') ||"
        "    setweight(to_tsvector('english', coalesce(content,        '')), 'D')"
        "  ) STORED,"
        "  ADD COLUMN content_tsv_simple tsvector GENERATED ALWAYS AS ("
        "    setweight(to_tsvector('simple', coalesce(qualified_name, '')), 'A') ||"
        "    setweight(to_tsvector('simple', coalesce(signature,      '')), 'B') ||"
        "    setweight(to_tsvector('simple', coalesce(doc_comment,    '')), 'C') ||"
        "    setweight(to_tsvector('simple', coalesce(content,        '')), 'D')"
        "  ) STORED"
    )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_tsv "
            "ON code_nodes USING gin (content_tsv)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_tsv_simple "
            "ON code_nodes USING gin (content_tsv_simple)"
        )
