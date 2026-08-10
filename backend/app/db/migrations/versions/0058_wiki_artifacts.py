"""Incremental wiki — persisted plan artifacts + per-page reuse stamps.

Revision ID: 0058_wiki_artifacts
Revises: 0057_code_nodes_parent_idx

Two pieces:

1. `wiki_artifacts` — one row per repository holding the Stage 2/1.5/3
   outputs (`overview` / `mindmap` / `plan` as JSONB) plus the reuse key:
   `structural_hash` (commit-free projection of the repo's shape),
   `wiki_schema_version` (hand-bumped pipeline version), and the chat /
   embed model ids. The incremental wiki path reuses the plan while the
   key matches; any mismatch falls back to a full rebuild.

2. Three nullable columns on `documents` stamped per wiki page at write
   time: `spec_hash` (canonical hash of the PageSpec the page was
   written against), `retrieval_fingerprint` (hash of the evidence
   bundle retrieved for the page), `wiki_schema_version`. Legacy rows
   stay NULL and read as "dirty" — no backfill needed (zero installs).

NB: revision IDs are persisted in `alembic_version.version_num
VARCHAR(32)` — `0058_wiki_artifacts` is 19 chars, safely under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_wiki_artifacts"
down_revision = "0057_code_nodes_parent_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wiki_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sync_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("repo_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_commit", sa.Text(), nullable=True),
        sa.Column("wiki_schema_version", sa.Integer(), nullable=False),
        sa.Column("structural_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("chat_model", sa.Text(), nullable=False),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column("overview", postgresql.JSONB(), nullable=False),
        sa.Column("mindmap", postgresql.JSONB(), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
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
        sa.UniqueConstraint("repository_id", name="uq_wiki_artifacts_repository"),
    )

    op.add_column(
        "documents",
        sa.Column("spec_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("retrieval_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("wiki_schema_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "wiki_schema_version")
    op.drop_column("documents", "retrieval_fingerprint")
    op.drop_column("documents", "spec_hash")
    op.drop_table("wiki_artifacts")
