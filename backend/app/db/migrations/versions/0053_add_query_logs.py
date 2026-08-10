"""Add query_logs table for user-facing search/retrieve observability.

Revision ID: 0053_add_query_logs
Revises: 0052_collapse_grants_oidc_sync

Distinct channel from `audit_events` — audit_events records
privileged admin actions; this records what users actually *ask*
cograph via search/retrieve from REST or MCP. The table feeds the
admin "Queries / Activity" page and per-user "My history".

Retention is enforced by a daily arq cron (`prune_query_logs` in
`backend/app/pipeline/worker.py`); see `RetentionSettings.query_logs_retention_days`.
The indexes below are sized for that sweep + the three admin filters
shipped in the first cut (by user, by repo, zero-result).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_add_query_logs"
down_revision = "0052_collapse_grants_oidc_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_logs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_query_logs_user_id_users",
            ),
            nullable=True,
        ),
        sa.Column("user_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "repository_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "repositories.id",
                ondelete="SET NULL",
                name="fk_query_logs_repository_id_repositories",
            ),
            nullable=True,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "md_collections.id",
                ondelete="SET NULL",
                name="fk_query_logs_collection_id_md_collections",
            ),
            nullable=True,
        ),
        sa.Column(
            "query_text",
            sa.String(length=256),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "query_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("client_label", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_check_constraint(
        "ck_query_logs_source",
        "query_logs",
        "source IN ('rest', 'mcp')",
    )
    op.create_check_constraint(
        "ck_query_logs_status",
        "query_logs",
        "status IN ('ok', 'empty', 'error')",
    )

    # Retention sweep — daily DELETE WHERE created_at < cutoff. Without
    # this index the sweep would sequential-scan, and on a 100k/day
    # account that becomes painful within a month.
    op.create_index(
        "ix_query_logs_created_at",
        "query_logs",
        ["created_at"],
    )

    # "My history" / admin-filter-by-user listing. Descending created_at
    # because the UI always shows newest first.
    op.create_index(
        "ix_query_logs_user_id_created_at",
        "query_logs",
        ["user_id", sa.text("created_at DESC")],
    )

    # Admin filter "queries against repo X".
    op.create_index(
        "ix_query_logs_repository_id_created_at",
        "query_logs",
        ["repository_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("repository_id IS NOT NULL"),
    )

    # Admin filter "queries that returned nothing". Partial — the
    # interesting set is tiny vs the table, so keeping the index small
    # matters more than covering every row.
    op.create_index(
        "ix_query_logs_zero_results",
        "query_logs",
        ["created_at"],
        postgresql_where=sa.text("result_count = 0"),
    )


def downgrade() -> None:
    op.drop_index("ix_query_logs_zero_results", table_name="query_logs")
    op.drop_index("ix_query_logs_repository_id_created_at", table_name="query_logs")
    op.drop_index("ix_query_logs_user_id_created_at", table_name="query_logs")
    op.drop_index("ix_query_logs_created_at", table_name="query_logs")
    op.drop_constraint("ck_query_logs_status", "query_logs", type_="check")
    op.drop_constraint("ck_query_logs_source", "query_logs", type_="check")
    op.drop_table("query_logs")
