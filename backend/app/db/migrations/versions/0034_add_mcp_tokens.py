"""Add mcp_tokens table.

Revision ID: 0034_add_mcp_tokens
Revises: 0033_add_users_is_owner

Personal access tokens for MCP clients (Claude Desktop, Cursor, etc.).
Each authenticated user can mint multiple tokens — they bear the user's
identity at the MCP transport layer (`/mcp`). The plaintext token is
shown once at creation; only its sha256 hash is stored.

`prefix` keeps the first ~8 characters of the plaintext for UI display
("cgr_AbCd…") so users can tell their tokens apart without seeing the
secret again. `last_used_at` is updated lazily by the auth middleware
(at most once per minute per token) for housekeeping.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0034_add_mcp_tokens"
down_revision = "0033_add_users_is_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index(
        "ix_mcp_tokens_user_id",
        "mcp_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
