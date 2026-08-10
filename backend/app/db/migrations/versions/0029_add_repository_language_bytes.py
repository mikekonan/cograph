"""Add repositories.language_bytes for full-repo language scan.

Revision ID: 0029_add_repository_language_bytes
Revises: 0028_drop_legacy_wiki_schema

Issue #66 — the Overview language breakdown was sourced from `source_files`
(graph-ingested only), so unsupported languages never made it onto the chart.
Persist a full-repo scan as JSONB on the repository so the API can return the
true composition.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0029_add_repo_language_bytes"
down_revision = "0028_drop_legacy_wiki_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "language_bytes",
            JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "language_bytes")
