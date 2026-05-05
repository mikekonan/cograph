"""Add indexes for repo enrichment queries and idempotency_keys table.

- Index on source_files(repository_id, language) to support the
  language_bytes GROUP BY aggregation in GET /api/repos/:host/:owner/:name.
- Index on repo_documents(repository_id) to support the documents_count
  COUNT query in GET /api/repos/:host/:owner/:name.
- New table `idempotency_keys` for POST /api/repos Idempotency-Key support.

down_revision intentionally targets 0008; reviewer will linearize this
alongside other parallel agent migrations before merging.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_repo_enrichment_indexes"
down_revision = "0010_llm_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- source_files(repository_id, language) composite index ---
    # Used by the language_bytes GROUP BY query in _build_repository_response.
    op.create_index(
        "ix_source_files_repo_language",
        "source_files",
        ["repository_id", "language"],
        unique=False,
    )

    # --- repo_documents(repository_id) index ---
    # Used by the documents_count SELECT COUNT(*) query.
    op.create_index(
        "ix_repo_documents_repository_id",
        "repo_documents",
        ["repository_id"],
        unique=False,
    )

    # --- idempotency_keys table ---
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "key_hash",
            sa.String(64),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "response_payload",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_idempotency_keys_user_id_users",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_key_hash",
        "idempotency_keys",
        ["key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_key_hash", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_repo_documents_repository_id", table_name="repo_documents")
    op.drop_index("ix_source_files_repo_language", table_name="source_files")
