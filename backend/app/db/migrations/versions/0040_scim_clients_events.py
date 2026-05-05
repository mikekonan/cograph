"""SCIM 2.0 deprovisioning — clients + events.

Revision ID: 0040_scim_clients_events
Revises: 0039_identity_providers_oidc

Phase 30.4 — SCIM 2.0 standard deprovisioning. When the IdP marks a user
inactive, every Cograph credential they hold dies in a single transaction
(refresh families dropped, PATs revoked with `reason='idp_block'`, audit
written, `users.is_active=false` flipped). Layer-1 enforcement at
`require_authenticated` reads `is_active` per request, so cookie sessions
also die at the next call without bus / cache plumbing.

Two new tables:

- `scim_clients` — bearer-token credentials minted per identity provider.
  Same wire shape as PATs (`cgr_pat_<48>`, raw SHA-256 hash, soft revoke)
  but a different audit lineage and never minted by a human via
  `/me/tokens`. Scoped to a single `identity_providers` row so disabling
  the provider also revokes its SCIM clients (cascade handled at
  application layer; `revoked_reason='provider_deleted'`).

- `scim_events` — per-request log with idempotency. The unique
  `idempotency_key` is computed in Python from `(provider_id, external_id,
  operation, sha256(canonical_payload))` so retry storms dedupe on intent,
  not arrival time. The unique violation is the dedupe signal — the
  handler returns the original response.

Local password auth and OIDC stay untouched. SCIM is purely a write
surface for IdP-driven deprovisioning.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0040_scim_clients_events"
down_revision = "0039_identity_providers_oidc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_clients",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "provider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="SET NULL",
                name="fk_scim_clients_provider_id_identity_providers",
            ),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['users:write']::text[]"),
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "length(token_prefix) >= 8",
            name="ck_scim_clients_token_prefix_len",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN "
            "('user','rotation','admin','provider_deleted')",
            name="ck_scim_clients_revoked_reason",
        ),
    )
    op.create_index(
        "ix_scim_clients_provider_active",
        "scim_clients",
        ["provider_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "scim_events",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "client_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "scim_clients.id",
                ondelete="SET NULL",
                name="fk_scim_events_client_id_scim_clients",
            ),
            nullable=True,
        ),
        sa.Column(
            "provider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="SET NULL",
                name="fk_scim_events_provider_id_identity_providers",
            ),
            nullable=True,
        ),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "target_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_scim_events_target_user_id_users",
            ),
            nullable=True,
        ),
        sa.Column("payload_hash", sa.LargeBinary(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "applied_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_scim_events_idempotency",
        ),
        sa.CheckConstraint(
            "operation IN ('create','replace','patch','delete')",
            name="ck_scim_events_operation",
        ),
        sa.CheckConstraint(
            "status IN ('applied','no_op','rejected')",
            name="ck_scim_events_status",
        ),
    )
    op.create_index(
        "ix_scim_events_target",
        "scim_events",
        ["target_user_id", "applied_at"],
    )
    op.create_index(
        "ix_scim_events_provider_applied",
        "scim_events",
        ["provider_id", "applied_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scim_events_provider_applied", table_name="scim_events")
    op.drop_index("ix_scim_events_target", table_name="scim_events")
    op.drop_table("scim_events")

    op.drop_index("ix_scim_clients_provider_active", table_name="scim_clients")
    op.drop_table("scim_clients")
