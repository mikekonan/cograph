"""Groups + per-resource ACL grants.

A `Group` is a named set of users. `RepositoryGrant` and
`CollectionGrant` give a group a `level` (read / write — NONE is the
absence of a grant row) on a single repository or md_collection. The
read-scope funnels in `backend.app.core.repository_access` and
`.md_collection_access` union these grants into the visible set for
USER-role accounts; OWNER/ADMIN role short-circuits always.

A group can optionally declare an OIDC mapping via
``(oidc_provider_id, oidc_group_name)``. When set as a pair, every
successful login against that identity provider adds the user to the
group if their ID-token ``groups`` claim contains the configured
name. Sync is additive — manual members stay; nothing is removed when
the IdP-side group changes. ``group_members.source`` records whether
each row was added manually or through OIDC.

The `level` column is a CHECK-constrained string rather than a native
enum to match the project's existing pattern (see `User.role` in
`backend/app/models/user.py` — same `native_enum=False`). The values
align 1:1 with `GrantLevel` in `backend/app/models/enums.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class Group(CreatedAtMixin, Base):
    __tablename__ = "groups"
    __table_args__ = (
        CheckConstraint(
            "(oidc_provider_id IS NULL) = (oidc_group_name IS NULL)",
            name="groups_oidc_mapping_paired",
        ),
        UniqueConstraint(
            "oidc_provider_id",
            "oidc_group_name",
            name="uq_groups_oidc_mapping",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    oidc_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    oidc_group_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )

    members = relationship(
        "GroupMember",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    repository_grants = relationship(
        "RepositoryGrant",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    collection_grants = relationship(
        "CollectionGrant",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    oidc_provider = relationship("IdentityProvider")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual', 'oidc')",
            name="group_members_source_check",
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        server_default=text("'manual'"),
    )

    group = relationship("Group", back_populates="members")


class RepositoryGrant(Base):
    __tablename__ = "repository_grants"
    __table_args__ = (
        CheckConstraint(
            "level IN ('read', 'write')",
            name="repository_grants_level_check",
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    group = relationship("Group", back_populates="repository_grants")


class CollectionGrant(Base):
    __tablename__ = "collection_grants"
    __table_args__ = (
        CheckConstraint(
            "level IN ('read', 'write')",
            name="collection_grants_level_check",
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    group = relationship("Group", back_populates="collection_grants")
