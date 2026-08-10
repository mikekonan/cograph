"""Add `log_queries` privacy flag to repositories.

Revision ID: 0054_repos_log_queries
Revises: 0053_add_query_logs

NB: revision IDs are persisted in `alembic_version.version_num
VARCHAR(32)`. A 34-char ID reached prod once and broke the UPDATE
that records the new head — that's why this name is terse. Future
hands: keep the revision string ≤32 characters.

Per-repo opt-out for the query-visibility log shipped in
`0053_add_query_logs`. When False, the async recorder
(`backend/app/query_logs/recorder.py`) drops the row instead of
enqueueing it. Default True keeps the prior behaviour for every
existing row, so no data backfill is required.

The flag is read by the recorder via a short-lived in-memory cache
(`query_log.repo_flag_cache_ttl_seconds`, default 30s) so toggling
the column does NOT require restarting the API — staleness is
bounded by the TTL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054_repos_log_queries"
down_revision = "0053_add_query_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "log_queries",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "log_queries")
