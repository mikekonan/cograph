"""Add repository document persistence for store #2."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_add_repo_documents"
down_revision = "0002_add_code_nodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "repository_id",
            "file_path",
            name="uq_repo_documents_repo_file_path",
        ),
    )
    op.create_table(
        "repo_document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column(
            "heading_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "mentions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["repo_documents.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_repo_document_chunks_document_chunk",
        ),
    )
    op.create_index(
        "idx_repo_documents_repository_id",
        "repo_documents",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "idx_repo_document_chunks_document_id",
        "repo_document_chunks",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_repo_document_chunks_document_id", table_name="repo_document_chunks")
    op.drop_index("idx_repo_documents_repository_id", table_name="repo_documents")
    op.drop_table("repo_document_chunks")
    op.drop_table("repo_documents")
