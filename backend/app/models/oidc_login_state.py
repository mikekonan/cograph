from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin


class OIDCLoginState(CreatedAtMixin, Base):
    """In-flight OIDC authorize state — PKCE + nonce + state.

    DB-backed instead of Redis because Cograph already treats Postgres as
    durable state and Redis as cache; this row must survive a worker
    restart between authorize and callback.

    `state_hash` is `sha256(state)` so the raw state value is never
    persisted — defense-in-depth against an attacker who reads the row.
    `consumed_at` flips on first use to defeat replay.
    """

    __tablename__ = "oidc_login_states"

    state_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "identity_providers.id",
            ondelete="CASCADE",
            name="fk_oidc_login_states_provider_id_identity_providers",
        ),
        nullable=False,
    )
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    return_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_oidc_login_states_initiated_user_id_users",
        ),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
