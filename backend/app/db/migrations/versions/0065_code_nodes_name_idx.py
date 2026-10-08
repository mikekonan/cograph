"""Index code_nodes (repository_id, name) for doc-to-symbol linking.

The repo-docs symbol linker looks up candidate nodes by bare `name` for
every doc chunk, on every sync, including unchanged docs. With no index on
`name` each lookup is a sequential scan of the whole table (all
repositories), so the step grows with total index size times chunk count.

NOTE on partial-failure recovery (same as 0057): if the CONCURRENT build
fails, drop the INVALID index (`SELECT indexrelid::regclass FROM pg_index
WHERE NOT indisvalid`) with DROP INDEX CONCURRENTLY and re-run the DDL.
"""

from __future__ import annotations

from alembic import op

revision = "0065_code_nodes_name_idx"
down_revision = "0064_wiki_cited_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_nodes_repo_name "
            "ON code_nodes (repository_id, name)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_code_nodes_repo_name")
