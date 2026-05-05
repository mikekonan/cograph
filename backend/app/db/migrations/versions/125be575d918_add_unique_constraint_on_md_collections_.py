"""add_unique_constraint_on_md_collections_name

Revision ID: 125be575d918
Revises: e712d18412a5
Create Date: 2026-04-27 21:32:20.157425
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = '125be575d918'
down_revision = 'e712d18412a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint('uq_md_collections_name', 'md_collections', ['name'])


def downgrade() -> None:
    op.drop_constraint('uq_md_collections_name', 'md_collections', type_='unique')
