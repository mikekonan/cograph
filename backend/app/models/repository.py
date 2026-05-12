from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin
from backend.app.models.enums import (
    RepoSource,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
)


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "host", "owner", "name", name="uq_repositories_host_owner_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    git_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[RepoSource] = mapped_column(
        Enum(
            RepoSource,
            native_enum=False,
            length=8,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RepoSource.GIT,
        server_default=text("'git'"),
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "git_hosts.id",
            ondelete="RESTRICT",
            name="fk_repositories_host_id_git_hosts",
        ),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    status: Mapped[RepositoryStatus] = mapped_column(
        Enum(
            RepositoryStatus,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RepositoryStatus.PENDING,
    )
    last_commit: Mapped[str | None] = mapped_column(String(64))
    visibility: Mapped[RepositoryVisibility] = mapped_column(
        Enum(
            RepositoryVisibility,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RepositoryVisibility.ADMIN_ONLY,
        server_default=text("'admin_only'"),
    )
    sync_schedule: Mapped[SyncSchedule] = mapped_column(
        Enum(
            SyncSchedule,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SyncSchedule.MANUAL,
    )
    sync_hour_utc: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=2,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set the moment a user clicks Delete; the synchronous handler flips
    # `status -> DELETING` + this timestamp and immediately returns 204,
    # while an arq worker drains the cascade. Read paths
    # (list endpoints, slug lookup, sync scheduler) MUST filter on
    # `deleted_at IS NULL` so a soft-deleted row is invisible from the
    # instant the user pressed the button.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    webhook_secret: Mapped[str | None] = mapped_column(String(255))
    error_msg: Mapped[str | None] = mapped_column(Text)
    graph_storage_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    # Issue #66 — full-repo language byte counts populated by the sync
    # pipeline's checkout walker. Keyed by canonical lowercase language name
    # (e.g. "go", "javascript", "makefile"). Null until the first sync runs.
    language_bytes: Mapped[dict[str, int] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    sync_runs = relationship(
        "RepoSyncRun",
        back_populates="repository",
        cascade="all, delete-orphan",
    )
    code_nodes = relationship(
        "CodeNode",
        cascade="all, delete-orphan",
    )
    repo_documents = relationship(
        "RepoDocument",
        cascade="all, delete-orphan",
    )
    documents = relationship(
        "Document",
        cascade="all, delete-orphan",
    )
