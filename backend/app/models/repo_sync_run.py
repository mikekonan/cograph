from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin
from backend.app.models.enums import RepoSyncRunStatus, RepoSyncTriggerKind


class RepoSyncRun(CreatedAtMixin, Base):
    __tablename__ = "repo_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_kind: Mapped[RepoSyncTriggerKind] = mapped_column(
        Enum(
            RepoSyncTriggerKind,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[RepoSyncRunStatus] = mapped_column(
        Enum(
            RepoSyncRunStatus,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RepoSyncRunStatus.QUEUED,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    requested_ref: Mapped[str | None] = mapped_column(String(64))
    # OWNER-requested full wiki rebuild: the processor passes
    # `force_full=True` to the wiki stage, bypassing all incremental reuse.
    wiki_rebuild_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    arq_job_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_msg: Mapped[str | None] = mapped_column(Text)

    repository = relationship("Repository", back_populates="sync_runs")
    requester = relationship("User", back_populates="sync_runs")
