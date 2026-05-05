"""add repository wiki rollout variant override

Revision ID: 0025_add_wiki_rollout_variant
Revises: 0024_add_evidence_packs_table
Create Date: 2026-04-23 20:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025_add_wiki_rollout_variant"
down_revision = "0024_add_evidence_packs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "wiki_default_variant",
            sa.Enum(
                "default",
                "preview",
                name="wikidefaultvariant",
                native_enum=False,
                length=16,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "wiki_default_variant")

