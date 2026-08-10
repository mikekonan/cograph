"""Add identity_providers.auto_link_on_verified_email.

Revision ID: 0048_add_idp_auto_link
Revises: 0047_drop_owner_singleton

When true, an OIDC login that collides with an existing local user's
email is auto-linked instead of refused with OIDC_LINK_REQUIRED. The
caller (oidc_provisioning) also clears password_hash and switches
auth_source to 'oidc', so the linked account becomes SSO-only.
Default false — opt-in per provider so the safe behavior stays the
default.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_add_idp_auto_link"
down_revision = "0047_drop_owner_singleton"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_providers",
        sa.Column(
            "auto_link_on_verified_email",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("identity_providers", "auto_link_on_verified_email")
