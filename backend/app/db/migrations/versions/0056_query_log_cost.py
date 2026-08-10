"""Per-query token + USD cost columns on query_logs.

Revision ID: 0056_query_log_cost
Revises: 0055_mcp_briefing

Adds five nullable columns:

- `tokens_input`        — input tokens consumed (embed prompt + any
                          completion input). Single field so we don't
                          have to fork "embed" vs "completion" in the
                          UI; per-model breakdown is the model column.
- `tokens_output`       — output tokens, populated only when a chat /
                          rerank model was called. Pure embed queries
                          leave this NULL.
- `cost_usd_micros`     — BIGINT storing USD × 10^6. NULL when we
                          don't have a price on file (operator using
                          self-hosted / Azure / a model we haven't
                          listed). 1 micro-USD = $0.000001, so a row
                          with cost_usd_micros=1234 cost the operator
                          $0.001234.
- `embed_model`         — model id of the embedding call. Snapshotted
                          per-row so renaming an assignment later
                          doesn't rewrite history.
- `completion_model`    — model id of any completion / rerank call
                          attached to this query. NULL when no chat
                          model was hit (the common retrieval path).

All five are NULLABLE and have no server_default, so the migration is
backfill-free — existing rows stay NULL and the new pricing card in
the admin UI explicitly explains "rows before <release date> have no
cost data".

NB: revision IDs are persisted in `alembic_version.version_num
VARCHAR(32)`. Keep the revision string ≤32 characters — a 34-char
form once reached prod and broke the UPDATE that records the new
head. `0056_query_log_cost` is 19 chars, safely under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_query_log_cost"
down_revision = "0055_mcp_briefing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_logs",
        sa.Column("tokens_input", sa.Integer(), nullable=True),
    )
    op.add_column(
        "query_logs",
        sa.Column("tokens_output", sa.Integer(), nullable=True),
    )
    op.add_column(
        "query_logs",
        sa.Column("cost_usd_micros", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "query_logs",
        sa.Column("embed_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "query_logs",
        sa.Column("completion_model", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_logs", "completion_model")
    op.drop_column("query_logs", "embed_model")
    op.drop_column("query_logs", "cost_usd_micros")
    op.drop_column("query_logs", "tokens_output")
    op.drop_column("query_logs", "tokens_input")
