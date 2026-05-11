from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin


class IdentityProvider(CreatedAtMixin, Base):
    """Owner-managed OIDC identity provider (Phase 30.3).

    Provider-agnostic — works with Okta, Auth0, Azure AD, Keycloak,
    Google Workspace, JumpCloud, etc. SAML is intentionally out of scope
    for V1. The `client_secret_encrypted` column stores Fernet-wrapped
    secrets; only `OIDCSecretCipher` can decrypt.

    `admin_group_mode` defaults to `ignore` — group → admin promotion is
    OFF unless the owner explicitly opts in. Group removal NEVER demotes
    (a lost group must not lock the owner out — owner action required).
    """

    __tablename__ = "identity_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="oidc")
    issuer_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=lambda: ["openid", "profile", "email"],
    )
    response_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="query",
    )
    groups_claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_allowlist: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text()).with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    auto_provision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_link_on_verified_email: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    admin_group_mode: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="ignore",
    )
    admin_groups: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text()).with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now().astimezone(),
    )
