"""Cached-prompt-token column on sync_jobs.

Revision ID: 0061_sync_job_cached_tok
Revises: 0060_sync_job_llm_cost

OpenAI bills prompt tokens served from the implicit prefix cache at a
~90% discount (`usage.prompt_tokens_details.cached_tokens`), and the
wiki agent loop is built around that cache (byte-identical system +
first-user anchor each turn). Until now the tally dropped the cached
count, so `cost_usd_micros` priced every prompt token at the full
input rate — overstating cache-heavy runs several-fold. The tally now
records the cached subset and the pricing maths bills it at the cached
rate; this column persists the per-step total.

Nullable: NULL means "predates cache accounting" (cost is an upper
bound) — distinct from a real 0 ("no cache hits").

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0061_sync_job_cached_tok` is 24 chars, under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_sync_job_cached_tok"
down_revision = "0060_sync_job_llm_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_jobs", sa.Column("tokens_cached", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_jobs", "tokens_cached")
