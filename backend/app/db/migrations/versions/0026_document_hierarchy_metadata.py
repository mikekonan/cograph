"""Add generated wiki hierarchy metadata to documents.

Revision ID: 0026_document_hierarchy_metadata
Revises: 0025_add_wiki_rollout_variant
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_document_hierarchy_metadata"
down_revision = "0025_add_wiki_rollout_variant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("parent_slug", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("page_kind", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("section_kind", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column(
            "variant", sa.String(length=32), nullable=False, server_default="default"
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "generation_version",
            sa.String(length=64),
            nullable=False,
            server_default="legacy-v1",
        ),
    )
    op.add_column("documents", sa.Column("source_commit", sa.Text(), nullable=True))
    op.execute(
        "UPDATE documents SET variant = 'preview', generation_version = 'preview' "
        "WHERE slug LIKE 'preview.%'"
    )
    op.execute(
        "UPDATE documents SET page_kind = doc_type, section_kind = doc_type "
        "WHERE page_kind IS NULL"
    )
    op.create_index(
        "idx_documents_repository_variant_sort",
        "documents",
        ["repository_id", "variant", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_documents_repository_variant_sort", table_name="documents")
    op.drop_column("documents", "source_commit")
    op.drop_column("documents", "generation_version")
    op.drop_column("documents", "variant")
    op.drop_column("documents", "section_kind")
    op.drop_column("documents", "page_kind")
    op.drop_column("documents", "parent_slug")
