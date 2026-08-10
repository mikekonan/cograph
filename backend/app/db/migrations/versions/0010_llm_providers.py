"""Add llm_providers table for LLM provider configuration.

Stores provider type, API URL, model selections, and encrypted key.
Raw keys are never returned through the API; only the hint (last 4 chars)
is derived application-side when required.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_llm_providers"
down_revision = "0009_sync_batches_and_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "provider_type",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "api_url",
            sa.Text,
            nullable=False,
        ),
        sa.Column(
            "chat_model",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "embed_model",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "api_key_encrypted",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
    )
    op.create_index(
        "ix_llm_providers_is_default",
        "llm_providers",
        ["is_default"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_providers_is_default", table_name="llm_providers")
    op.drop_table("llm_providers")
