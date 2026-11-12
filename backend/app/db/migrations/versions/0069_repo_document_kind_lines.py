"""repo_documents.kind and repo_document_chunks.start_line / end_line.

Revision ID: 0069_repo_document_kind_lines
Revises: 0068_query_log_error_wiki

`kind` records the RepoDocumentKind a document was indexed as. It is
written only when a document is created or its content changes: NULL
means "written before this column existed", and no backfill runs because
an UPDATE would move `updated_at`, which the search date filters read.
The line columns let a search hit point at `path:start-end` for chunks
cut by line (YAML). All nullable; adding them rewrites nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0069_repo_document_kind_lines"
down_revision = "0068_query_log_error_wiki"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repo_documents", sa.Column("kind", sa.String(32), nullable=True))
    op.add_column("repo_document_chunks", sa.Column("start_line", sa.Integer(), nullable=True))
    op.add_column("repo_document_chunks", sa.Column("end_line", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("repo_document_chunks", "end_line")
    op.drop_column("repo_document_chunks", "start_line")
    op.drop_column("repo_documents", "kind")
