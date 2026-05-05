"""Add users.is_owner flag.

Revision ID: 0033_add_users_is_owner
Revises: 0032_merge_md_rag

The first user — created via /auth/bootstrap — is the *owner*. Owners
are admins with one extra bit: they cannot be demoted or deleted by
other admins. Without this guarantee, a freshly-promoted admin could
lock out the original installer, which we don't want.

Backfill rule: the single oldest admin gets is_owner=true. Every other
row gets false. This produces the same shape that bootstrap+CLI flows
will produce going forward, and survives `alembic upgrade` on existing
deployments where the first admin already exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0033_add_users_is_owner"
down_revision = "0032_merge_md_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Backfill: oldest admin in the system becomes the owner.
    op.execute(
        sa.text(
            """
            UPDATE users
               SET is_owner = TRUE
             WHERE id = (
                 SELECT id
                   FROM users
                  WHERE role = 'admin'
                  ORDER BY created_at ASC
                  LIMIT 1
             )
            """,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_owner")
