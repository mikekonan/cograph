"""Add content_updated_at to md_documents

Revision ID: e712d18412a5
Revises: 6c89e762b2bb
Create Date: 2026-04-27 18:50:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e712d18412a5'
down_revision = '6c89e762b2bb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('md_documents', sa.Column('content_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('md_documents', 'content_updated_at')
