"""Drop owner-singleton constraint; OWNER becomes a label.

Revision ID: 0047_drop_owner_singleton
Revises: 0046_widen_md_job_kind

OWNER and ADMIN are merged into one privilege tier (any owner-only
endpoint becomes admin-or-owner). OWNER stays as a value in the
user_role enum so the bootstrap user keeps a visible marker, but the
partial unique index that enforced "exactly one owner" is dropped —
duplicate owner rows are now harmless because the role grants no extra
permissions over admin.

The role CHECK still allows {owner, admin, user}; we keep the value
for label semantics and to avoid touching existing data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_drop_owner_singleton"
down_revision = "0046_widen_md_job_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_users_single_owner", table_name="users")


def downgrade() -> None:
    op.create_index(
        "uq_users_single_owner",
        "users",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
