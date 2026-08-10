from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class UserIdentity(CreatedAtMixin, Base):
    """Link between a Cograph user and an IdP `sub` claim.

    UNIQUE on (provider_id, subject) so multiple IdP rows can share an
    `issuer_url` (e.g. two Okta apps in the same Cograph instance) — the
    same `sub` may legitimately appear under different `provider_id`s.

    `email_at_link` is a snapshot of the IdP-asserted email at link time
    for audit purposes; it does not constrain the user's current email.
    """

    __tablename__ = "user_identities"

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
            name="fk_user_identities_user_id_users",
        ),
        nullable=False,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "identity_providers.id",
            ondelete="CASCADE",
            name="fk_user_identities_provider_id_identity_providers",
        ),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    email_at_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user = relationship("User", lazy="joined")
    provider = relationship("IdentityProvider", lazy="joined")
