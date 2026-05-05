"""Phase 8b — derived bank facts/entities/observations layer."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0021_add_bank_fact_tables"
down_revision = "0020_add_code_node_temporal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "bank_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["bank_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "canonical_name",
            "entity_type",
            name="uq_bank_entities_document_canonical_type",
        ),
    )
    op.create_index(
        "idx_bank_entities_bank_doc",
        "bank_entities",
        ["bank_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "idx_bank_entities_bank_canonical",
        "bank_entities",
        ["bank_id", "canonical_name"],
        unique=False,
    )

    op.create_table(
        "bank_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("heading_path", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("extraction_model", sa.Text(), nullable=False, server_default=""),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["bank_id"], ["banks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["bank_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["bank_document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "ALTER TABLE bank_facts "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )
    op.execute(
        "ALTER TABLE bank_facts ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS ("
        "  setweight(to_tsvector('english', coalesce(statement, '')), 'A') ||"
        "  setweight(to_tsvector('english', coalesce(source_excerpt, '')), 'B')"
        ") STORED"
    )
    op.create_index(
        "idx_bank_facts_bank_doc",
        "bank_facts",
        ["bank_id", "document_id"],
        unique=False,
    )

    op.create_table(
        "bank_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["bank_facts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["bank_entities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_bank_observations_fact", "bank_observations", ["fact_id"], unique=False)
    op.create_index(
        "idx_bank_observations_entity",
        "bank_observations",
        ["entity_id"],
        unique=False,
    )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bank_facts_hnsw "
            "ON bank_facts USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bank_facts_tsv "
            "ON bank_facts USING gin (content_tsv)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_bank_facts_tsv")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_bank_facts_hnsw")

    op.drop_index("idx_bank_observations_entity", table_name="bank_observations")
    op.drop_index("idx_bank_observations_fact", table_name="bank_observations")
    op.drop_table("bank_observations")

    op.drop_index("idx_bank_facts_bank_doc", table_name="bank_facts")
    op.drop_column("bank_facts", "content_tsv")
    op.drop_table("bank_facts")

    op.drop_index("idx_bank_entities_bank_canonical", table_name="bank_entities")
    op.drop_index("idx_bank_entities_bank_doc", table_name="bank_entities")
    op.drop_table("bank_entities")
