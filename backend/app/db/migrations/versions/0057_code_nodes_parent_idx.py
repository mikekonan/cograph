"""Phase 22 — index code_nodes.parent_id to make self-FK cascade cheap.

Without this index, DELETE on any code_node row with descendants triggers
a sequential scan per cascaded child (the fk_code_nodes_parent_id_code_nodes
ON DELETE CASCADE trigger). Measured on prod 2026-05-19: ~297s for a single
DELETE on a module with 1764 descendants in a 130k-row table — hits the
300s statement_timeout in backend/app/db/session.py and aborts the entire
reindex transaction.

Partial index `WHERE parent_id IS NOT NULL` — top-level module nodes have
no parent and never participate in this cascade lookup, so they don't need
an index entry. Saves ~30-40% of index size, matches the style of the
existing idx_code_nodes_role partial index.

NOTE on partial-failure recovery (same as 0015 / 0018)
======================================================
CREATE INDEX CONCURRENTLY runs inside an autocommit_block. If a CONCURRENT
build fails mid-way Alembic still marks the revision as applied because
there's no preceding transactional step to undo. Recovery:

    SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

then DROP INDEX CONCURRENTLY on the invalid row and re-run the DDL below
manually — do NOT call alembic downgrade (it would also be CONCURRENT and
race on the same lock).
"""
from __future__ import annotations

from alembic import op


revision = "0057_code_nodes_parent_idx"
down_revision = "0056_query_log_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_code_nodes_parent_id "
            "ON code_nodes (parent_id) "
            "WHERE parent_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_code_nodes_parent_id")
