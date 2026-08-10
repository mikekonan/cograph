"""Add explicit runtime provider assignments for completion and embeddings."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0022_add_llm_runtime_settings"
down_revision = "0021_add_bank_fact_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_runtime_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("completion_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("embedding_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["completion_provider_id"],
            ["llm_providers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_provider_id"],
            ["llm_providers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_runtime_settings_completion_provider_id",
        "llm_runtime_settings",
        ["completion_provider_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_runtime_settings_embedding_provider_id",
        "llm_runtime_settings",
        ["embedding_provider_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO llm_runtime_settings (
            id,
            completion_provider_id,
            embedding_provider_id,
            created_at,
            updated_at
        )
        SELECT
            1,
            id,
            id,
            now(),
            now()
        FROM llm_providers
        WHERE is_default = true
        LIMIT 1
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_runtime_settings_embedding_provider_id",
        table_name="llm_runtime_settings",
    )
    op.drop_index(
        "ix_llm_runtime_settings_completion_provider_id",
        table_name="llm_runtime_settings",
    )
    op.drop_table("llm_runtime_settings")
