"""Add users.is_active / auth_source / deactivation columns.

Revision ID: 0037_users_active_authsource
Revises: 0036_owner_role_audit

Phase 30.1 part 2 — adds the cross-cutting columns that every later
sub-phase reuses:

- `is_active` (default TRUE) — single source of truth checked on every
  authenticated request. Phase 30.4 (SCIM) flips this to FALSE.
- `auth_source` — 'password' (existing rows) or 'oidc' (Phase 30.3).
- `deactivated_at` / `deactivated_reason` — populated when SCIM or an
  admin disables the user.
- `last_login_at` — touched on every successful login.

`password` is made nullable so OIDC-provisioned users (Phase 30.3) can
have NULL. The column is the underlying name for `User.password_hash`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0037_users_active_authsource"
down_revision = "0036_owner_role_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "auth_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'password'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_auth_source",
        "users",
        "auth_source IN ('password', 'oidc')",
    )
    op.add_column(
        "users",
        sa.Column("deactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deactivated_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.alter_column("users", "password", nullable=True)

    op.create_index(
        "ix_users_active_email",
        "users",
        ["email"],
        unique=False,
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_active_email", table_name="users")

    # Re-tighten password to NOT NULL — drop OIDC users first; we have no
    # safe backfill for a NULL password.
    op.execute(sa.text("DELETE FROM users WHERE password IS NULL"))
    op.alter_column("users", "password", nullable=False)

    op.drop_column("users", "last_login_at")
    op.drop_column("users", "deactivated_reason")
    op.drop_column("users", "deactivated_at")
    op.drop_constraint("ck_users_auth_source", "users", type_="check")
    op.drop_column("users", "auth_source")
    op.drop_column("users", "is_active")
