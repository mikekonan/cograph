"""Phase 19 foundation — persisted EvidencePack storage.

Adds the durable ``evidence_packs`` table used by the typed evidence pipeline
and additive API/MCP brief surfaces. Each row stores one structured pack payload
plus quality metadata, snapshot freshness, and hash-driven incremental state.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0024_add_evidence_packs_table"
down_revision = "0023_add_repository_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_packs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=True),
        sa.Column("pack_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("quality", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("verified_commit", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["repo_sync_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "pack_id",
            name="uq_evidence_packs_repo_pack",
        ),
    )
    op.create_index(
        "idx_evidence_packs_repo_kind",
        "evidence_packs",
        ["repository_id", "kind"],
        unique=False,
    )
    op.create_index(
        "idx_evidence_packs_repo_sort",
        "evidence_packs",
        ["repository_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_evidence_packs_repo_sort", table_name="evidence_packs")
    op.drop_index("idx_evidence_packs_repo_kind", table_name="evidence_packs")
    op.drop_table("evidence_packs")
