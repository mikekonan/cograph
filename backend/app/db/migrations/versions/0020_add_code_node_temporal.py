"""Phase 8c — code node temporal metadata for retrieval provenance/filters."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_add_code_node_temporal"
down_revision = "0019_add_documents_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("code_nodes", sa.Column("first_seen_commit", sa.Text(), nullable=True))
    op.add_column("code_nodes", sa.Column("last_changed_commit", sa.Text(), nullable=True))
    op.add_column(
        "code_nodes",
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_code_nodes_repo_last_changed_at",
        "code_nodes",
        ["repository_id", "last_changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_code_nodes_repo_last_changed_at", table_name="code_nodes")
    op.drop_column("code_nodes", "last_changed_at")
    op.drop_column("code_nodes", "last_changed_commit")
    op.drop_column("code_nodes", "first_seen_commit")
