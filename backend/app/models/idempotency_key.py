from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class IdempotencyKey(Base):
    """Short-lived deduplication record for idempotent POST endpoints.

    The `key_hash` column stores SHA256(idempotency_key + user_id) so the raw
    header value is never persisted. `response_payload` is NULL until the
    first request completes; replays during that window still get is_replay=True
    but payload=None (caller must handle gracefully).

    Rows older than 24 h are pruned opportunistically by the helper in
    `backend.app.core.idempotency`.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
