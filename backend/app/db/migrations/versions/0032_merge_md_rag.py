"""merge md-rag chain with repository_source head

The wiki-llm-rewrite branch advanced through 0028..0031 while the
markdown-rag branch added five md_rag migrations off 0027. Merge them
into a single head so alembic upgrade can run cleanly.

Revision ID: 0032_merge_md_rag
Revises: 0031_add_repository_source, 125be575d918
Create Date: 2026-05-01
"""
from __future__ import annotations


revision = "0032_merge_md_rag"
down_revision = ("0031_add_repository_source", "125be575d918")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
