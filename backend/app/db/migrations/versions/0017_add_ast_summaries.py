"""Add code_node_summaries and code_subgraph_summaries tables for Phase 7c AST summaries.

code_node_summaries — one LLM-generated summary per code_node, with a
PageRank-derived importance score and content/neighbour hashes for incremental
regeneration.

code_subgraph_summaries — LLM-generated summary for a neighbourhood subgraph
centred on a root node, stored with the full member_node_ids UUID array so the
scorer can efficiently walk related summaries.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_ast_summaries"
down_revision = "0016_add_chunk_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_node_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("importance", sa.Double, nullable=False, server_default="0.0"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("neighbor_hash", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["code_node_id"], ["code_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "code_node_id", name="uq_code_node_summaries_code_node_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX idx_code_node_summaries_repo_importance "
        "ON code_node_summaries (repository_id, importance DESC)"
    )

    op.create_table(
        "code_subgraph_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "member_node_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("importance", sa.Double, nullable=False, server_default="0.0"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["root_node_id"], ["code_nodes.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "repository_id",
            "root_node_id",
            name="uq_code_subgraph_summaries_repo_root",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX idx_code_subgraph_summaries_repo_importance "
        "ON code_subgraph_summaries (repository_id, importance DESC)"
    )


def downgrade() -> None:
    op.drop_table("code_subgraph_summaries")
    op.drop_table("code_node_summaries")
