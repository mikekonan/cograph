"""Per-step LLM token/cost accounting columns on sync_jobs.

Revision ID: 0060_sync_job_llm_cost
Revises: 0059_wiki_rebuild_flag

The pipeline now tallies `resp.usage` per stage (embed.code, summaries,
wiki.write, …) and the processor stamps the rollup onto the owning
`sync_jobs` row when the step completes. All columns are nullable:
NULL means "no LLM calls / predates accounting", distinct from a real
zero. `cost_usd_micros` is integer micro-USD (see `llm/pricing.py`);
`cost_breakdown` maps stage label → {calls, tokens_in, tokens_out,
model, cost_usd_micros}.

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0060_sync_job_llm_cost` is 22 chars, under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0060_sync_job_llm_cost"
down_revision = "0059_wiki_rebuild_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("tokens_input", sa.Integer(), nullable=True))
    op.add_column("sync_jobs", sa.Column("tokens_output", sa.Integer(), nullable=True))
    op.add_column(
        "sync_jobs", sa.Column("cost_usd_micros", sa.BigInteger(), nullable=True)
    )
    op.add_column("sync_jobs", sa.Column("llm_model", sa.String(128), nullable=True))
    op.add_column(
        "sync_jobs",
        sa.Column(
            "cost_breakdown",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sync_jobs", "cost_breakdown")
    op.drop_column("sync_jobs", "llm_model")
    op.drop_column("sync_jobs", "cost_usd_micros")
    op.drop_column("sync_jobs", "tokens_output")
    op.drop_column("sync_jobs", "tokens_input")
