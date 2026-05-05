"""Per-role API secrets — Phase 30.7.

Revision ID: 0043_llm_secrets
Revises: 0042_llm_model_assignments

Replaces ``llm_providers`` (which bundled api_url + api_key + chat/embed
model names into a single row) with ``llm_secrets`` — reusable boxes of
(api_url + encrypted api_key). Each ``llm_model_assignments`` row picks
its own secret + writes its own ``model_name`` so the four runtime roles
can point at independent credentials (e.g. embedding/fast/writer share
one OpenAI key, reasoning uses a different provider with a different
key).

This is a rip-and-replace migration with no data preservation. Tables get
dropped and recreated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0043_llm_secrets"
down_revision = "0042_llm_model_assignments"
branch_labels = None
depends_on = None


_VALID_ROLES = (
    "embedding",
    "completion_fast",
    "completion_writer",
    "completion_reasoning",
)
_VALID_EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh")


def upgrade() -> None:
    op.drop_table("llm_embedding_state")
    op.drop_index(
        "ix_llm_model_assignments_provider_id",
        table_name="llm_model_assignments",
    )
    op.drop_table("llm_model_assignments")
    op.drop_table("llm_providers")

    op.create_table(
        "llm_secrets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_llm_secrets"),
        sa.UniqueConstraint("name", name="uq_llm_secrets_name"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_llm_secrets_updated_by",
        ),
    )

    op.create_table(
        "llm_model_assignments",
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "secret_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column(
            "extra_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("role", name="pk_llm_model_assignments"),
        sa.ForeignKeyConstraint(
            ["secret_id"],
            ["llm_secrets.id"],
            ondelete="RESTRICT",
            name="fk_llm_model_assignments_secret",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_llm_model_assignments_updated_by",
        ),
        sa.CheckConstraint(
            f"role IN {tuple(_VALID_ROLES)}",
            name="chk_llm_model_assignments_role",
        ),
        sa.CheckConstraint(
            f"reasoning_effort IS NULL OR reasoning_effort IN {tuple(_VALID_EFFORTS)}",
            name="chk_llm_model_assignments_effort_value",
        ),
        sa.CheckConstraint(
            "(role = 'embedding' AND embedding_dim = 1536) "
            "OR (role <> 'embedding' AND embedding_dim IS NULL)",
            name="chk_llm_model_assignments_embedding_dim",
        ),
        sa.CheckConstraint(
            "reasoning_effort IS NULL OR role = 'completion_reasoning'",
            name="chk_llm_model_assignments_effort_role",
        ),
    )

    op.create_index(
        "ix_llm_model_assignments_secret_id",
        "llm_model_assignments",
        ["secret_id"],
        unique=False,
    )

    op.create_table(
        "llm_embedding_state",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "current_secret_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("current_model_name", sa.Text(), nullable=True),
        sa.Column("current_dim", sa.Integer(), nullable=True),
        sa.Column(
            "last_reembed_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_reembed_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_reembed_actor",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_embedding_state"),
        sa.ForeignKeyConstraint(
            ["current_secret_id"],
            ["llm_secrets.id"],
            ondelete="SET NULL",
            name="fk_llm_embedding_state_secret",
        ),
        sa.ForeignKeyConstraint(
            ["last_reembed_actor"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_llm_embedding_state_actor",
        ),
        sa.CheckConstraint("id = 1", name="chk_llm_embedding_state_singleton"),
    )

    op.execute("INSERT INTO llm_embedding_state (id) VALUES (1)")


def downgrade() -> None:
    raise NotImplementedError(
        "0043_llm_secrets is forward-only; the prior llm_providers schema is "
        "incompatible with per-role secrets and zero-installs forbids data-"
        "preserving downgrades."
    )
