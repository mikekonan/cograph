"""Collapse grant levels to read/write + OIDC group sync mapping.

Revision ID: 0052_collapse_grants_oidc_sync
Revises: 0051_add_sync_batches_run_id

Two related changes shipped together:

1. **GrantLevel.ADMIN goes away.** It was operationally murky — grant
   administration (handing out new grants) was always gated on
   OWNER/ADMIN role, never on per-resource ADMIN-grant. Operators
   actually want a binary: "can this group read?" vs "can this group
   run jobs?". Existing ADMIN-grants demote to WRITE (the highest
   remaining level); destructive endpoints that used to require an
   ADMIN-grant now require OWNER/ADMIN role at the dependency layer.

2. **OIDC group sync.** Each ``groups`` row may declare an
   ``(oidc_provider_id, oidc_group_name)`` pair. On every successful
   OIDC login the user is added to every cograph group whose pair
   matches a claim in their ID token. Additive only — never removes.
   ``group_members.source`` records ``'oidc'`` vs ``'manual'`` so the
   admin UI can show provenance.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_collapse_grants_oidc_sync"
down_revision = "0051_add_sync_batches_run_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Tighten grant level CHECKs from {read, write, admin} → {read, write}.
    # Demote existing ADMIN grants to WRITE (highest remaining level).
    op.execute("UPDATE repository_grants SET level = 'write' WHERE level = 'admin'")
    op.execute("UPDATE collection_grants SET level = 'write' WHERE level = 'admin'")

    op.drop_constraint(
        "repository_grants_level_check", "repository_grants", type_="check"
    )
    op.create_check_constraint(
        "repository_grants_level_check",
        "repository_grants",
        "level IN ('read', 'write')",
    )

    op.drop_constraint(
        "collection_grants_level_check", "collection_grants", type_="check"
    )
    op.create_check_constraint(
        "collection_grants_level_check",
        "collection_grants",
        "level IN ('read', 'write')",
    )

    # --- 2. Add OIDC sync mapping on groups.
    op.add_column(
        "groups",
        sa.Column(
            "oidc_provider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="SET NULL",
                name="fk_groups_oidc_provider_id_identity_providers",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "groups",
        sa.Column("oidc_group_name", sa.String(length=256), nullable=True),
    )

    # Both-or-neither: the OIDC mapping is meaningful only as a pair.
    op.create_check_constraint(
        "groups_oidc_mapping_paired",
        "groups",
        "(oidc_provider_id IS NULL) = (oidc_group_name IS NULL)",
    )

    # Same (provider, group_name) cannot point to two cograph groups.
    op.create_unique_constraint(
        "uq_groups_oidc_mapping",
        "groups",
        ["oidc_provider_id", "oidc_group_name"],
    )

    # The lookup hot path on login: WHERE oidc_provider_id=? AND oidc_group_name IN (...).
    # Partial — most groups in practice are manual, so the index stays small.
    op.create_index(
        "ix_groups_oidc_lookup",
        "groups",
        ["oidc_provider_id", "oidc_group_name"],
        postgresql_where=sa.text("oidc_provider_id IS NOT NULL"),
    )

    # --- 3. Mark synced memberships so we can audit and (later) deprovision.
    # GroupMember.added_by is FK → users.id; can't reuse it to record
    # "added by OIDC sync". A separate provenance column keeps the
    # audit trail honest without overloading existing FKs.
    op.add_column(
        "group_members",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_check_constraint(
        "group_members_source_check",
        "group_members",
        "source IN ('manual', 'oidc')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "group_members_source_check", "group_members", type_="check"
    )
    op.drop_column("group_members", "source")

    op.drop_index("ix_groups_oidc_lookup", table_name="groups")
    op.drop_constraint("uq_groups_oidc_mapping", "groups", type_="unique")
    op.drop_constraint("groups_oidc_mapping_paired", "groups", type_="check")
    op.drop_column("groups", "oidc_group_name")
    op.drop_column("groups", "oidc_provider_id")

    op.drop_constraint(
        "collection_grants_level_check", "collection_grants", type_="check"
    )
    op.create_check_constraint(
        "collection_grants_level_check",
        "collection_grants",
        "level IN ('read', 'write', 'admin')",
    )
    op.drop_constraint(
        "repository_grants_level_check", "repository_grants", type_="check"
    )
    op.create_check_constraint(
        "repository_grants_level_check",
        "repository_grants",
        "level IN ('read', 'write', 'admin')",
    )
