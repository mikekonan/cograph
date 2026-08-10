from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin


class GitCredential(TimestampMixin, Base):
    """Operator PAT for one git host (Phase 30.5).

    Token plaintext is held in memory only on create / rotate; the
    persisted column is `token_encrypted` (Fernet via
    `GitCredentialCipher`). Test button records the result on the row
    so the FE can surface freshness without re-running.

    `is_default` is partially-unique-per-host: at most one row marked
    default per `host_id`. Routing always picks the default row, so
    swapping the operator amounts to flipping which row is default
    rather than re-binding repos.
    """

    __tablename__ = "git_credentials"
    __table_args__ = (
        CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN "
            "('ok','unauthorized','forbidden','network')",
            name="ck_git_credentials_last_test_status",
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
            name="fk_git_credentials_host_id_git_hosts",
        ),
        nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
            name="fk_git_credentials_owner_user_id_users",
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    scopes_observed: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text()).with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    host = relationship("GitHost", lazy="joined")
