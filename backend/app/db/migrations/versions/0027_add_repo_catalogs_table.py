"""Add repo_catalogs table.

Revision ID: 0027_add_repo_catalogs_table
Revises: 0026_document_hierarchy_metadata
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_add_repo_catalogs_table"
down_revision = "0026_document_hierarchy_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_catalogs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "repository_id", sa.Uuid(as_uuid=True), nullable=False
        ),
        sa.Column("sync_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("verified_commit", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_repo_catalogs_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["repo_sync_runs.id"],
            name=op.f("fk_repo_catalogs_sync_run_id_repo_sync_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_catalogs")),
        sa.UniqueConstraint(
            "repository_id",
            name=op.f("uq_repo_catalogs_repository_id"),
        ),
    )
    op.create_index(
        op.f("idx_repo_catalogs_repo"),
        "repo_catalogs",
        ["repository_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("idx_repo_catalogs_repo"), table_name="repo_catalogs"
    )
    op.drop_table("repo_catalogs")
