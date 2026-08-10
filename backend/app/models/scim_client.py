from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class SCIMClient(CreatedAtMixin, Base):
    """Bearer-token credential for SCIM 2.0 deprovisioning (Phase 30.4).

    Same wire shape as a PAT (`cgr_pat_<48>`, raw SHA-256 hash, soft
    revoke) but lives in its own table because:

    1. SCIM clients are per-IdP and never minted by a human via
       `/me/tokens` — different audit lineage.
    2. Disabling an `identity_providers` row at the application layer
       cascades to its SCIM clients (`revoked_reason='provider_deleted'`)
       without touching `personal_access_tokens`.
    3. SCIM bearer tokens are scope-limited to `users:write` (extensible
       in future; nothing reads other scopes today).
    """

    __tablename__ = "scim_clients"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "identity_providers.id",
            ondelete="SET NULL",
            name="fk_scim_clients_provider_id_identity_providers",
        ),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=lambda: ["users:write"],
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    provider = relationship("IdentityProvider", lazy="joined")
