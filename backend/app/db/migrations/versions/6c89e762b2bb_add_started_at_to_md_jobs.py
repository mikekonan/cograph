"""Add started_at to md_jobs

Revision ID: 6c89e762b2bb
Revises: 6050517e0201
Create Date: 2026-04-27 14:37:30.178390
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6c89e762b2bb'
down_revision = '6050517e0201'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('md_jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('md_jobs', 'started_at')
