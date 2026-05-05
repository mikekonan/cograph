"""Add documents.quality JSONB column for wiki quality telemetry.

Revision ID: 0030_add_documents_quality
Revises: 0029_add_repo_language_bytes

PR4 of the Wiki quality v2 program (Phase 29.1). Per-page metrics emitted by
the LLM-driven pipeline (`backend/app/wiki/pipeline.py`) — citation
counts, unresolved-placeholder counts, low-confidence chunk counts,
covers_questions, has_diagram, manifest_entries_used — land here so the
frontend can surface chip-level quality signal without a fresh DB roundtrip
or a recompute.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0030_add_documents_quality"
down_revision = "0029_add_repo_language_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "quality",
            JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "quality")
