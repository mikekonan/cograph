"""sync_batches.run_id FK to repo_sync_runs.

Revision ID: 0051_add_sync_batches_run_id
Revises: 0050_add_groups_and_acl

Adds an explicit `run_id` FK on `sync_batches` pointing at
`repo_sync_runs.id`. Until this revision the relationship was implicit
— `RepoSyncOrchestrator.enqueue_repository_sync` creates the run and
the batch in the same transaction with the same `repository_id`, but
nothing in the schema records the pairing.

The stale-sweep cron (recovering OOMKilled mid-run zombie rows) and the
new force-cancel API both need to walk run → owning batch → in-flight
jobs in one query. Without this FK the only join is a fragile
`repository_id + created_at` window, which breaks once a repo has
accumulated multiple historic batches.

ON DELETE SET NULL so that purging a `repo_sync_run` doesn't cascade
into batch history — `sync_batches` keeps its independent lifecycle
(jobs are pruned via `SyncBatch.jobs` cascade-delete).

Legacy backfill: for every existing batch, find the run created on the
same `repository_id` within ±5 s of `sync_batches.created_at` (the
orchestrator commits both inside a few milliseconds; 5 s is generous
headroom). Rows with no match stay NULL — the two new consumers fall
back to the timestamp window for one release.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_add_sync_batches_run_id"
down_revision = "0050_add_groups_and_acl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_batches",
        sa.Column(
            "run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repo_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_sync_batches_run_id",
        "sync_batches",
        ["run_id"],
    )
    op.execute(
        """
        UPDATE sync_batches sb
           SET run_id = r.id
          FROM repo_sync_runs r
         WHERE sb.repository_id = r.repository_id
           AND sb.created_at BETWEEN r.created_at - interval '5 seconds'
                                 AND r.created_at + interval '5 seconds'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sync_batches_run_id", table_name="sync_batches")
    op.drop_column("sync_batches", "run_id")
