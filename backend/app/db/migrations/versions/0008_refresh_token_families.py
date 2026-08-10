"""Add refresh_token_families table for refresh rotation and reuse detection.

Tracks the currently-valid jti per refresh-token family.
On reuse detection (presented jti != current_jti), revoked_at is set to block
the entire family.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_refresh_token_families"
down_revision = "0007_code_edges_mentions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_token_families",
        sa.Column(
            "family",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "current_jti",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_refresh_token_families_user_id_users",
        ),
    )
    op.create_index(
        "ix_refresh_token_families_user_id",
        "refresh_token_families",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_token_families_user_id", table_name="refresh_token_families")
    op.drop_table("refresh_token_families")
