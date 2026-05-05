"""Add sync_batches and sync_jobs tables for step-level pipeline telemetry.

sync_batches: one row per end-to-end repo sync (or confluence export /
bank import in the future). Carries kind, trigger, label, and aggregate
status.

sync_jobs: one row per pipeline step within a batch. Carries step name,
per-step progress (0-100), units (done/total/unit), error fields, and
retry metadata.

repo_sync_runs is kept as-is; it is still used by the existing pipeline
orchestrator/processor until a follow-up migration removes it after the
new step telemetry is verified in production.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_sync_batches_and_jobs"
down_revision = "0008_refresh_token_families"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_batches",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'repo_sync'"),
        ),
        sa.Column(
            "trigger",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column("label", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("bank_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
            name="fk_sync_batches_repository_id_repositories",
        ),
    )
    op.create_index(
        "ix_sync_batches_repository_id",
        "sync_batches",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "ix_sync_batches_created_at",
        "sync_batches",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "sync_jobs",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("bank_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("units_total", sa.Integer(), nullable=True),
        sa.Column("units_done", sa.Integer(), nullable=True),
        sa.Column("units_unit", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["sync_batches.id"],
            ondelete="CASCADE",
            name="fk_sync_jobs_batch_id_sync_batches",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
            name="fk_sync_jobs_repository_id_repositories",
        ),
    )
    op.create_index(
        "ix_sync_jobs_batch_id",
        "sync_jobs",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_sync_jobs_repository_id",
        "sync_jobs",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "ix_sync_jobs_status",
        "sync_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_sync_jobs_step",
        "sync_jobs",
        ["step"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_jobs_step", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_status", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_repository_id", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_batch_id", table_name="sync_jobs")
    op.drop_table("sync_jobs")

    op.drop_index("ix_sync_batches_created_at", table_name="sync_batches")
    op.drop_index("ix_sync_batches_repository_id", table_name="sync_batches")
    op.drop_table("sync_batches")
