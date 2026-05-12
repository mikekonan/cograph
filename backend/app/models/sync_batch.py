from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin
from backend.app.models.enums import SyncBatchKind, SyncBatchTrigger, SyncJobStatus

if TYPE_CHECKING:
    from backend.app.models.sync_job import SyncJob


class SyncBatch(CreatedAtMixin, Base):
    """One end-to-end sync run composed of multiple SyncJob step-rows.

    kind=repo_sync is the default. confluence_export is a future add-on;
    the column already exists so the migration stays backward compatible.
    """

    __tablename__ = "sync_batches"
    __table_args__ = (
        Index("ix_sync_batches_repository_id", "repository_id"),
        Index("ix_sync_batches_created_at", "created_at"),
        Index("ix_sync_batches_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind: Mapped[SyncBatchKind] = mapped_column(
        Enum(
            SyncBatchKind,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SyncBatchKind.REPO_SYNC,
    )
    trigger: Mapped[SyncBatchTrigger] = mapped_column(
        Enum(
            SyncBatchTrigger,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SyncBatchTrigger.MANUAL,
    )
    # Human-readable label, e.g. "fastapi/fastapi" for repo_sync.
    label: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # Set for repo_sync / confluence_export.
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=True,
    )

    # The repo_sync_run this batch belongs to. Set in
    # RepoSyncOrchestrator.enqueue_repository_sync after the run is flushed
    # so its id is available; NULL on legacy batches that pre-date
    # migration 0051 and on rows whose run got hard-deleted (the FK is
    # ON DELETE SET NULL so batch history survives independently).
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repo_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[SyncJobStatus] = mapped_column(
        Enum(
            SyncJobStatus,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SyncJobStatus.QUEUED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationship uses string ref to avoid circular import.
    jobs: Mapped[list["SyncJob"]] = relationship(  # noqa: F821
        "SyncJob",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="SyncJob.created_at",
    )
