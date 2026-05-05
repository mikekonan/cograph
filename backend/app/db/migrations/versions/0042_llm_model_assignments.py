"""Per-role LLM runtime assignments + embedding state row.

Revision ID: 0042_llm_model_assignments
Revises: 0041_git_hosts_credentials

Phase 30.6 — replace the singleton ``llm_runtime_settings`` row (one chat
model + one embedding model for everything) with a per-role assignment
table covering four runtime roles, plus a single-row state table that
records what the corpus is **currently** embedded with so the FE can
surface a re-embed banner when the assignment drifts away from state.

Schema additions
================

``llm_model_assignments``
-------------------------
- PK is ``role`` itself (4-row max). Roles:
  - ``embedding`` — RAG ingest + query
  - ``completion_fast`` — classifiers / suggestions
  - ``completion_writer`` — wiki + chat answers
  - ``completion_reasoning`` — wiki Stage 4d/4e (when shipped)
- ``provider_id`` ON DELETE RESTRICT — provider can't be removed while
  any role still points at it; the API surfaces this as
  ``409 PROVIDER_IN_USE``.
- ``reasoning_effort`` is gated by a CHECK so only the
  ``completion_reasoning`` role may carry one.
- ``embedding_dim`` is hard-locked to 1536 for the embedding role and
  must be NULL for completion roles. Switching to 3072
  (``text-embedding-3-large``) requires migrating the pgvector column —
  explicit V2 work, not a config flip.
- ``extra_params`` is a JSONB blob the resolver merges onto the OpenAI
  request kwargs (``temperature``, ``top_p``, ...). Defaults to ``{}``.

``llm_embedding_state``
-----------------------
- Single-row guard (``id = 1``). Records what the corpus *was* embedded
  with — diverges from the assignment row during a re-embed.
- ``current_dim`` is recorded so a future change to embedding dim is
  visible (even though we currently lock it to 1536).

Drop ``llm_runtime_settings`` without data preservation; existing installs
should recreate the runtime assignment rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0042_llm_model_assignments"
down_revision = "0041_git_hosts_credentials"
branch_labels = None
depends_on = None


_VALID_ROLES = (
    "embedding",
    "completion_fast",
    "completion_writer",
    "completion_reasoning",
)
_VALID_EFFORTS = ("low", "medium", "high")


def upgrade() -> None:
    op.create_table(
        "llm_model_assignments",
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "provider_id",
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
            ["provider_id"],
            ["llm_providers.id"],
            ondelete="RESTRICT",
            name="fk_llm_model_assignments_provider",
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
        "ix_llm_model_assignments_provider_id",
        "llm_model_assignments",
        ["provider_id"],
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
            "current_provider_id",
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
            ["current_provider_id"],
            ["llm_providers.id"],
            ondelete="SET NULL",
            name="fk_llm_embedding_state_provider",
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

    op.drop_table("llm_runtime_settings")


def downgrade() -> None:
    op.create_table(
        "llm_runtime_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "completion_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "embedding_provider_id",
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

    op.drop_table("llm_embedding_state")
    op.drop_index(
        "ix_llm_model_assignments_provider_id",
        table_name="llm_model_assignments",
    )
    op.drop_table("llm_model_assignments")
