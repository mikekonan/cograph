from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin
from backend.app.models.enums import SyncJobStatus, SyncStep

if TYPE_CHECKING:
    from backend.app.models.sync_batch import SyncBatch


class SyncJob(CreatedAtMixin, Base):
    """One pipeline step-job within a SyncBatch.

    Field semantics (aligned with FE contract):
    - progress: 0-100 while running/success/skipped, null while queued
    - units_total / units_done / units_unit: materialised when job starts; null while queued
    - error_code / error_msg: set for terminal non-success states that need detail
      (for example error or capability-disabled skipped steps)
    - attempt: starts at 1, incremented by retry
    """

    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index("ix_sync_jobs_batch_id", "batch_id"),
        Index("ix_sync_jobs_repository_id", "repository_id"),
        Index("ix_sync_jobs_status", "status"),
        Index("ix_sync_jobs_step", "step"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sync_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised FK so single-repo queries don't need a join.
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=True,
    )

    step: Mapped[SyncStep] = mapped_column(
        Enum(
            SyncStep,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # Human-readable title, e.g. "Parse source (tree-sitter)".
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")

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

    # Progress 0-100; null while queued.
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Units materialised when step starts.
    units_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # LLM usage attributed to this step (null = step made no LLM calls or
    # predates accounting). `cost_usd_micros` stays null for models without
    # a price on file — "no price", not "free". `tokens_cached` is the
    # cached-prompt-read subset of `tokens_input` (billed at the cached
    # rate; null also on rows from before cache accounting — their cost
    # is an upper bound). `cost_breakdown` maps stage label →
    # {calls, tokens_in, tokens_out, tokens_cached, model, cost_usd_micros}.
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cost_breakdown: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch: Mapped["SyncBatch"] = relationship(  # noqa: F821
        "SyncBatch",
        back_populates="jobs",
    )
