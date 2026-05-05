from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class SCIMEvent(Base):
    """Per-request SCIM log row (Phase 30.4).

    Idempotency model: `idempotency_key` is computed in Python from
    `(provider_id, external_id|'-', operation, sha256_hex(payload))` and
    enforced by a unique constraint. Retries arrive with the same key →
    second insert raises IntegrityError → handler returns the original
    SCIM response (RFC 7644 §3.4.2 idempotent semantics).

    `applied_at` is excluded from the key on purpose: clock-skewed
    retries 30 s apart still describe the same operation.
    """

    __tablename__ = "scim_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "scim_clients.id",
            ondelete="SET NULL",
            name="fk_scim_events_client_id_scim_clients",
        ),
        nullable=True,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "identity_providers.id",
            ondelete="SET NULL",
            name="fk_scim_events_provider_id_identity_providers",
        ),
        nullable=True,
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_scim_events_target_user_id_users",
        ),
        nullable=True,
    )
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
