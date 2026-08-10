"""add repository visibility

Revision ID: 0023_add_repository_visibility
Revises: 0022_add_llm_runtime_settings
Create Date: 2026-04-22 02:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_add_repository_visibility"
down_revision = "0022_add_llm_runtime_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="admin_only",
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "visibility")
