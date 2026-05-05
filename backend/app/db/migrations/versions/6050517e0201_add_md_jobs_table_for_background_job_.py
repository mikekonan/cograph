"""Add md_jobs table for background job tracking

Revision ID: 6050517e0201
Revises: 2a54ef01f78c
Create Date: 2026-04-27 14:13:59.936207
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6050517e0201'
down_revision = '2a54ef01f78c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('md_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('collection_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('embed', 'resolve_links', name='mdjobkind', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'success', 'error', name='mdjobstatus', native_enum=False, length=32), nullable=False),
    sa.Column('result_summary', sa.JSON(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['collection_id'], ['md_collections.id'], name=op.f('fk_md_jobs_collection_id_md_collections'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_md_jobs'))
    )
    op.create_index(op.f('idx_md_jobs_collection_id'), 'md_jobs', ['collection_id'], unique=False)
    op.create_index(op.f('idx_md_jobs_status'), 'md_jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('idx_md_jobs_status'), table_name='md_jobs')
    op.drop_index(op.f('idx_md_jobs_collection_id'), table_name='md_jobs')
    op.drop_table('md_jobs')
