from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class PersonalAccessToken(CreatedAtMixin, Base):
    """Unified personal access token (Phase 30.2).

    Authenticates both REST and MCP. Plaintext format is
    `cgr_pat_<48 random base64url bytes>`. The DB stores raw SHA-256 of
    the plaintext — uninvertible by birthday-bound argument (288-bit
    secret), so no pepper or HMAC is needed.

    Scopes (closed set, enforced by DB CHECK):
    - `api:read`  — gates GET endpoints under the `require_scope` gate.
    - `api:write` — gates mutating REST endpoints.
    - `mcp`       — gates MCP transport access.

    Cookie / bearer-JWT actors implicitly hold ALL scopes; only PAT
    actors are gated by the row's `scopes` column.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
            name="fk_personal_access_tokens_user_id_users",
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String()).with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user = relationship("User", lazy="joined")
