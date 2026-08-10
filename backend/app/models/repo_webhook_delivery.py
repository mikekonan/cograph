from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class RepoWebhookDelivery(Base):
    """Per-delivery dedup row (Phase 30.5).

    GitHub retries failed deliveries with the same `X-GitHub-Delivery`
    header value; the unique constraint collapses retries onto one row
    so one logical event enqueues at most one sync job.

    `applied_at`-ish field is `received_at` to distinguish from the
    push-payload timestamp (which the producer controls).
    """

    __tablename__ = "repo_webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "host_id", "delivery_id", name="uq_webhook_delivery"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "git_hosts.id",
            ondelete="CASCADE",
            name="fk_repo_webhook_deliveries_host_id_git_hosts",
        ),
        nullable=False,
    )
    delivery_id: Mapped[str] = mapped_column(Text, nullable=False)
    repo_full_name: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now().astimezone(),
    )
    sync_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
