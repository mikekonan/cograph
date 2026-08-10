"""Phase 8 foundation — generated wiki documents storage.

Adds the durable ``documents`` table used by the AST-first wiki generator.
Each row stores the generated markdown body, stable slug/order metadata, the
source hashes used for incremental skips, and JSON citation/source metadata for
the later `/wiki` API/UI slice.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_add_documents_table"
down_revision = "0018_trgm_and_simple_tsv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("source_node_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_repo_doc_chunk_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("citations", sa.JSON(), nullable=False, server_default="[]"),
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
        sa.UniqueConstraint("repository_id", "slug", name="uq_documents_repo_slug"),
    )
    op.create_index(
        "idx_documents_repository_sort",
        "documents",
        ["repository_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_documents_repository_sort", table_name="documents")
    op.drop_table("documents")
