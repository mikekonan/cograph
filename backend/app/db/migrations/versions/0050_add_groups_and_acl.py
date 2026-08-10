"""Groups + per-resource ACL grants.

Revision ID: 0050_add_groups_and_acl
Revises: 0049_add_repository_deleted_at

Adds a tenant-level RBAC layer on top of the existing
visibility (PUBLIC / ADMIN_ONLY) model. Four new tables:

* `groups`              — named set of users.
* `group_members`       — user ↔ group membership.
* `repository_grants`   — (group, repository) → level.
* `collection_grants`   — (group, md_collection) → level.

`level` is a CHECK-constrained string column rather than a native enum
to match the project's existing convention (see `User.role`).

The layer is **purely additive**: zero rows in any of these tables
means the access funnel behaves exactly as before. PUBLIC repos and
collections stay visible to everyone; OWNER/ADMIN role short-circuits
in the funnel. USER-role accounts gain visibility iff they belong to
a group with a matching grant.

SCIM group sync is deliberately out of scope here — the existing
`scim_clients` model provisions users only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_add_groups_and_acl"
down_revision = "0049_add_repository_deleted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_groups_created_by_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_groups_name"),
    )

    op.create_table(
        "group_members",
        sa.Column(
            "group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "groups.id",
                ondelete="CASCADE",
                name="fk_group_members_group_id_groups",
            ),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
                name="fk_group_members_user_id_users",
            ),
            primary_key=True,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "added_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_group_members_added_by_users",
            ),
            nullable=True,
        ),
    )
    # Per-user "which groups am I in" lookup — driven by every read
    # request through the ACL funnel.
    op.create_index(
        "ix_group_members_user_id",
        "group_members",
        ["user_id"],
    )

    op.create_table(
        "repository_grants",
        sa.Column(
            "group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "groups.id",
                ondelete="CASCADE",
                name="fk_repository_grants_group_id_groups",
            ),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "repositories.id",
                ondelete="CASCADE",
                name="fk_repository_grants_repository_id_repositories",
            ),
            primary_key=True,
        ),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "granted_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_repository_grants_granted_by_users",
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "level IN ('read', 'write', 'admin')",
            name="repository_grants_level_check",
        ),
    )
    # Funnel semijoin: `repositories.id IN (SELECT repository_id FROM
    # repository_grants WHERE group_id IN (...))`.
    op.create_index(
        "ix_repository_grants_repository_id",
        "repository_grants",
        ["repository_id"],
    )
    # Admin UI listing: "what does group X have access to?".
    op.create_index(
        "ix_repository_grants_group_id",
        "repository_grants",
        ["group_id"],
    )

    op.create_table(
        "collection_grants",
        sa.Column(
            "group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "groups.id",
                ondelete="CASCADE",
                name="fk_collection_grants_group_id_groups",
            ),
            primary_key=True,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "md_collections.id",
                ondelete="CASCADE",
                name="fk_collection_grants_collection_id_md_collections",
            ),
            primary_key=True,
        ),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "granted_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_collection_grants_granted_by_users",
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "level IN ('read', 'write', 'admin')",
            name="collection_grants_level_check",
        ),
    )
    op.create_index(
        "ix_collection_grants_collection_id",
        "collection_grants",
        ["collection_id"],
    )
    op.create_index(
        "ix_collection_grants_group_id",
        "collection_grants",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collection_grants_group_id", table_name="collection_grants"
    )
    op.drop_index(
        "ix_collection_grants_collection_id", table_name="collection_grants"
    )
    op.drop_table("collection_grants")
    op.drop_index(
        "ix_repository_grants_group_id", table_name="repository_grants"
    )
    op.drop_index(
        "ix_repository_grants_repository_id", table_name="repository_grants"
    )
    op.drop_table("repository_grants")
    op.drop_index("ix_group_members_user_id", table_name="group_members")
    op.drop_table("group_members")
    op.drop_table("groups")
