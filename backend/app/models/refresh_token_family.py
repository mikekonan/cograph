from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin


class RefreshTokenFamily(CreatedAtMixin, Base):
    """
    Tracks the currently-valid jti for each refresh-token family.

    One row per family UUID. On every successful refresh the `current_jti` is
    updated to the newly-issued token's jti. If a token arrives whose jti does
    NOT match `current_jti`, reuse has been detected and `revoked_at` is set,
    blocking the entire family.

    This implements refresh-token rotation and reuse detection.
    """

    __tablename__ = "refresh_token_families"

    family: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    current_jti: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
