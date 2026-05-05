"""Owner role enum + audit_events.

Revision ID: 0036_owner_role_audit
Revises: 0035_add_repository_host

Phase 30.1 — replaces the implicit `is_owner` boolean with an explicit
`owner` value in the user_role enum, gated by a partial unique that
forbids more than one owner row at a time. Also creates the
`audit_events` table that every privileged sub-phase under Phase 30
writes to.

The `users.role` column is `Enum(..., native_enum=False, length=16)` —
SQLAlchemy emits it as `VARCHAR(16)` with a CHECK constraint, NOT a
Postgres ENUM type. So there is no `ALTER TYPE` to run; we just rebuild
the CHECK with the new value list and backfill the column.

Backfill rule: any existing `is_owner=TRUE` row (the one set by the
historical bootstrap flow) gets `role='owner'`. The single-owner
partial unique then guarantees at most one owner ever.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0036_owner_role_audit"
down_revision = "0035_add_repository_host"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Backfill role=owner for the historical is_owner=TRUE row.
    op.execute(
        sa.text("UPDATE users SET role = 'owner' WHERE is_owner = TRUE")
    )

    # 2. Drop the old role CHECK and recreate with the extended value set.
    #    SQLAlchemy's Enum(native_enum=False) generates a CHECK named
    #    `ck_users_role` (per the naming convention). We drop with IF EXISTS
    #    to stay portable across fresh installs and pre-existing DBs where
    #    the constraint may have been auto-named.
    op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('owner', 'admin', 'user')",
    )

    # 3. Single-owner partial unique.
    op.create_index(
        "uq_users_single_owner",
        "users",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )

    # 4. Drop the legacy boolean.
    op.drop_column("users", "is_owner")

    # 5. Audit events.
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_events_actor_user_id_users"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_audit_events_target_user_id_users"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'info'"),
        ),
        sa.Column(
            "metadata_json",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_audit_events_severity",
        ),
    )
    op.create_index(
        "ix_audit_events_actor_user_id",
        "audit_events",
        ["actor_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_events_target_user_id",
        "audit_events",
        ["target_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_events_event_type",
        "audit_events",
        ["event_type", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_target_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.add_column(
        "users",
        sa.Column(
            "is_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(sa.text("UPDATE users SET is_owner = TRUE WHERE role = 'owner'"))
    op.execute(sa.text("UPDATE users SET role = 'admin' WHERE role = 'owner'"))

    op.drop_index("uq_users_single_owner", table_name="users")

    op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'user')",
    )
