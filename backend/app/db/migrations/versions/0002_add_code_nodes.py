"""Add code node persistence for the graph engine."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_add_code_nodes"
down_revision = "0001_core_infra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_nodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("doc_comment", sa.Text(), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "callers",
            postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "callees",
            postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            ["parent_id"],
            ["code_nodes.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_code_nodes_repo_file",
        "code_nodes",
        ["repository_id", "file_path"],
        unique=False,
    )
    op.create_index(
        "uq_code_nodes_repo_qualified_name",
        "code_nodes",
        ["repository_id", "qualified_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_code_nodes_repo_qualified_name", table_name="code_nodes")
    op.drop_index("idx_code_nodes_repo_file", table_name="code_nodes")
    op.drop_table("code_nodes")
