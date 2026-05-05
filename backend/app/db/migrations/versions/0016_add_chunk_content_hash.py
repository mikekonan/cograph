"""Add content_hash to repo_document_chunks and bank_document_chunks.

Enables the incremental skip predicate in RepoDocumentEmbedderService and
BankDocumentEmbedderService: a chunk whose content_hash + model already match
the live values is skipped, avoiding redundant embedding API calls on re-runs.

Existing rows get an empty string default — they will be re-embedded once on
the next pipeline/upload run and their content_hash will then be populated.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_add_chunk_content_hash"
down_revision = "0015_add_bm25_tsvector_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repo_document_chunks",
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "bank_document_chunks",
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("bank_document_chunks", "content_hash")
    op.drop_column("repo_document_chunks", "content_hash")
