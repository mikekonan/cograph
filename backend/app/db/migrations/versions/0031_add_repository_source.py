"""Add repositories.source enum column for git vs zip-uploaded repos.

Revision ID: 0031_add_repository_source
Revises: 0030_add_documents_quality

Phase 31 — file-upload ingest path. A repository is now either a `git`
clone (the legacy default) or a `zip` snapshot uploaded via the new
`POST /repos/upload` endpoint. The downstream pipeline branches on this
column to either run `git pull` or re-extract a stored archive.

The unique constraint on `(git_url, branch)` is preserved — for zip
sources the API generates a deterministic `git_url=zip://{repository_id}`
plus `branch="upload"` so the constraint never collides.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031_add_repository_source"
down_revision = "0030_add_documents_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "source",
            sa.String(length=8),
            nullable=False,
            server_default="git",
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "source")
