"""Extract code graph edges and doc mentions into dedicated tables.

``code_edges`` replaces the inline ``code_nodes.callers`` / ``callees``
arrays with one row per edge. ``target_qualified_name`` is the source
of truth (always set); ``target_node_id`` is the resolved cache and
becomes NULL via ``ON DELETE SET NULL`` when the target node goes
away, so inbound references survive across file reindex cycles.

``repo_document_chunk_mentions`` replaces the ``repo_document_chunks``
.mentions array with a proper join table so we can index, filter, and
partial-update without rewriting the whole chunk row.

Legacy columns (``code_nodes.callers``, ``code_nodes.callees``,
``repo_document_chunks.mentions``) are kept during cutover; the
finalize migration drops them once every repository has flipped to
``graph_storage_version = 2``.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_code_edges_mentions"
down_revision = "0006_code_nodes_ranges"
branch_labels = None
depends_on = None


EDGE_TYPE_VALUES = ("calls", "inherits", "imports", "declares")


def upgrade() -> None:
    edge_type_list = ", ".join(f"'{value}'" for value in EDGE_TYPE_VALUES)

    op.create_table(
        "code_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_qualified_name", sa.Text(), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
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
            ["source_node_id"],
            ["code_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"],
            ["code_nodes.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            f"edge_type IN ({edge_type_list})",
            name="ck_code_edges_edge_type",
        ),
        sa.UniqueConstraint(
            "source_node_id",
            "edge_type",
            "target_qualified_name",
            name="uq_code_edges_source_type_target",
        ),
    )
    op.create_index(
        "idx_code_edges_source",
        "code_edges",
        ["source_node_id", "edge_type"],
        unique=False,
    )
    op.create_index(
        "idx_code_edges_target",
        "code_edges",
        ["target_node_id", "edge_type"],
        unique=False,
        postgresql_where=sa.text("target_node_id IS NOT NULL"),
    )
    op.create_index(
        "idx_code_edges_unresolved",
        "code_edges",
        ["repository_id", "target_qualified_name"],
        unique=False,
        postgresql_where=sa.text("target_node_id IS NULL"),
    )

    op.create_table(
        "repo_document_chunk_mentions",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["repo_document_chunks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["code_node_id"],
            ["code_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "code_node_id"),
    )
    op.create_index(
        "idx_repo_document_chunk_mentions_node",
        "repo_document_chunk_mentions",
        ["code_node_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_repo_document_chunk_mentions_node",
        table_name="repo_document_chunk_mentions",
    )
    op.drop_table("repo_document_chunk_mentions")

    op.drop_index("idx_code_edges_unresolved", table_name="code_edges")
    op.drop_index("idx_code_edges_target", table_name="code_edges")
    op.drop_index("idx_code_edges_source", table_name="code_edges")
    op.drop_table("code_edges")
