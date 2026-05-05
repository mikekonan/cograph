"""Create auth, repository, and sync-run tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_core_infra"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "repositories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("git_url", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column(
            "branch",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_commit", sa.Text(), nullable=True),
        sa.Column(
            "sync_schedule",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "sync_hour_utc",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_secret", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("git_url", "branch", name="uq_repositories_git_url_branch"),
    )
    op.create_index(
        "idx_repositories_next_sync",
        "repositories",
        ["next_sync_at"],
        unique=False,
        postgresql_where=sa.text("sync_schedule IN ('hourly', 'daily', 'weekly')"),
    )

    op.create_table(
        "repo_sync_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_ref", sa.Text(), nullable=True),
        sa.Column("arq_job_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
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
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_repo_sync_runs_repo_created",
        "repo_sync_runs",
        ["repository_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_repo_sync_runs_status_created",
        "repo_sync_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uniq_repo_sync_runs_active",
        "repo_sync_runs",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uniq_repo_sync_runs_active", table_name="repo_sync_runs")
    op.drop_index("idx_repo_sync_runs_status_created", table_name="repo_sync_runs")
    op.drop_index("idx_repo_sync_runs_repo_created", table_name="repo_sync_runs")
    op.drop_table("repo_sync_runs")

    op.drop_index("idx_repositories_next_sync", table_name="repositories")
    op.drop_table("repositories")

    op.drop_table("users")
