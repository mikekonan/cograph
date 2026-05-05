"""Add md_rag collections, documents, chunks, links

Revision ID: 2a54ef01f78c
Revises: 0027_add_repo_catalogs_table
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2a54ef01f78c"
down_revision = "0027_add_repo_catalogs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- md_collections ---
    op.create_table(
        "md_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "visibility",
            sa.Enum(
                "private",
                "public",
                "admin_only",
                name="mdcollectionvisibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_md_collections_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_collections")),
    )

    # --- md_documents ---
    op.create_table(
        "md_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=True),
        sa.Column("frontmatter", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("heading_tree", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("code_blocks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tables", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("links", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["md_collections.id"],
            name=op.f("fk_md_documents_collection_id_md_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_documents")),
        sa.UniqueConstraint(
            "collection_id",
            "source_key",
            name="uq_md_documents_collection_source_key",
        ),
    )

    # --- md_chunks ---
    op.create_table(
        "md_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("heading_level", sa.Integer(), nullable=True),
        sa.Column("section_anchor", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column(
            "model",
            sa.String(),
            server_default="",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["md_documents.id"],
            name=op.f("fk_md_chunks_document_id_md_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_md_chunks_document_chunk",
        ),
    )

    # Retype embedding to pgvector vector(1536).
    op.execute(
        "ALTER TABLE md_chunks "
        "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )

    # Generated tsvector for BM25.
    op.execute(
        "ALTER TABLE md_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED"
    )

    # --- md_links ---
    op.create_table(
        "md_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("target_document_id", sa.Uuid(), nullable=True),
        sa.Column("link_text", sa.Text(), nullable=True),
        sa.Column("href", sa.Text(), nullable=False),
        sa.Column(
            "link_type",
            sa.Enum(
                "wiki",
                "markdown",
                "absolute",
                name="mdlinktype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["md_documents.id"],
            name=op.f("fk_md_links_source_document_id_md_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_document_id"],
            ["md_documents.id"],
            name=op.f("fk_md_links_target_document_id_md_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_links")),
        sa.UniqueConstraint(
            "source_document_id",
            "href",
            name="uq_md_links_source_href",
        ),
    )

    # --- indexes ---
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_md_chunks_hnsw "
            "ON md_chunks USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_md_chunks_tsv "
            "ON md_chunks USING gin (content_tsv)"
        )

    op.create_index(
        op.f("idx_md_documents_collection_id"),
        "md_documents",
        ["collection_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_md_links_source_document_id"),
        "md_links",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("idx_md_links_target_document_id"),
        "md_links",
        ["target_document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("idx_md_links_target_document_id"), table_name="md_links"
    )
    op.drop_index(
        op.f("idx_md_links_source_document_id"), table_name="md_links"
    )
    op.drop_index(
        op.f("idx_md_documents_collection_id"), table_name="md_documents"
    )

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_md_chunks_tsv")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_md_chunks_hnsw")

    op.drop_table("md_links")
    op.execute("ALTER TABLE md_chunks DROP COLUMN content_tsv")
    op.drop_table("md_chunks")
    op.drop_table("md_documents")
    op.drop_table("md_collections")

    op.execute("DROP TYPE IF EXISTS mdcollectionvisibility")
    op.execute("DROP TYPE IF EXISTS mdlinktype")
