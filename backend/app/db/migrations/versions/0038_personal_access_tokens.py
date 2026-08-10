"""Drop mcp_tokens, add personal_access_tokens.

Revision ID: 0038_personal_access_tokens
Revises: 0037_users_active_authsource

Phase 30.2 — replaces the MCP-only `mcp_tokens` table with a unified
`personal_access_tokens` table that authenticates both REST and MCP.

Format: `cgr_pat_<48 random base64url bytes>`. Hash is raw SHA-256 of the
plaintext (288-bit secret = uninvertible by definition; no HMAC, no
pepper). `token_prefix` keeps the first 16 chars of the plaintext for UI
disambiguation.

Scopes: `api:read`, `api:write`, `mcp`. The CHECK enforces the closed
set; cardinality > 0 forbids empty arrays. Soft revoke via `revoked_at`
+ `revoked_reason`.

This migration intentionally does not preserve legacy MCP token data;
existing `cgr_<43>` tokens become invalid, users re-mint as `cgr_pat_<48>`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0038_personal_access_tokens"
down_revision = "0037_users_active_authsource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop mcp_tokens — no data preservation (zero-installs constraint).
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")

    # 2. Create personal_access_tokens.
    op.create_table(
        "personal_access_tokens",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
                name="fk_personal_access_tokens_user_id_users",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.UniqueConstraint("token_hash", name="uq_personal_access_tokens_token_hash"),
        sa.CheckConstraint(
            "scopes <@ ARRAY['api:read', 'api:write', 'mcp']::text[]",
            name="ck_personal_access_tokens_scopes_subset",
        ),
        sa.CheckConstraint(
            "cardinality(scopes) > 0",
            name="ck_personal_access_tokens_scopes_nonempty",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN "
            "('user', 'rotation', 'admin', 'idp_block', 'role_change')",
            name="ck_personal_access_tokens_revoked_reason",
        ),
    )
    op.create_index(
        "ix_personal_access_tokens_active",
        "personal_access_tokens",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_personal_access_tokens_token_prefix",
        "personal_access_tokens",
        ["token_prefix"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_access_tokens_token_prefix", table_name="personal_access_tokens"
    )
    op.drop_index(
        "ix_personal_access_tokens_active", table_name="personal_access_tokens"
    )
    op.drop_table("personal_access_tokens")

    # Recreate the legacy mcp_tokens shape so a downgrade is idempotent
    # against test fixtures, even though we have no production data path.
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_mcp_tokens_user_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
