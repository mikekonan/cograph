"""Owner-managed identity provider CRUD (Phase 30.3).

Mounted under `/api/admin/identity-providers`. Read endpoints are
admin-or-owner; write endpoints are owner-only — IdP changes affect the
entire instance and are sensitive (client secret access).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.oidc_cipher import OIDCSecretCipher
from backend.app.auth.oidc_client import OIDCClient
from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    get_settings_dep,
    require_admin_or_owner,
    require_csrf,
)
from backend.app.core.errors import ApiError
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.user import User
from backend.app.models.user_identity import UserIdentity

router = APIRouter(prefix="/admin/identity-providers", tags=["admin", "oidc"])

_ADMIN_GROUP_MODES = {"ignore", "owner_approval", "owner_delegated"}
_RESPONSE_MODES = {"query", "form_post"}
_KINDS = {"oidc"}


class IdentityProviderView(BaseModel):
    id: UUID
    slug: str
    display_name: str
    kind: str
    issuer_url: str
    client_id: str
    has_client_secret: bool
    scopes: list[str]
    response_mode: str
    groups_claim: str | None
    domain_allowlist: list[str] | None
    auto_provision: bool
    auto_link_on_verified_email: bool
    admin_group_mode: str
    admin_groups: list[str] | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class IdentityProviderListResponse(BaseModel):
    providers: list[IdentityProviderView]


class IdentityProviderTestResponse(BaseModel):
    issuer_ok: bool
    jwks_ok: bool
    issuer_url: str
    authorization_endpoint: str | None
    token_endpoint: str | None
    jwks_keys: int
    error: str | None = None


class CreateIdentityProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-_]*$")
    display_name: str = Field(min_length=1, max_length=120)
    kind: str = "oidc"
    issuer_url: str = Field(min_length=8, max_length=512)
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str | None = Field(default=None, max_length=2048)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    response_mode: str = "query"
    groups_claim: str | None = Field(default=None, max_length=128)
    domain_allowlist: list[str] | None = None
    auto_provision: bool = True
    auto_link_on_verified_email: bool = False
    admin_group_mode: str = "ignore"
    admin_groups: list[str] | None = None
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"Unknown kind '{value}'. Allowed: {sorted(_KINDS)}")
        return value

    @field_validator("response_mode")
    @classmethod
    def _check_response_mode(cls, value: str) -> str:
        if value not in _RESPONSE_MODES:
            raise ValueError(
                f"Unknown response_mode '{value}'. Allowed: {sorted(_RESPONSE_MODES)}"
            )
        return value

    @field_validator("admin_group_mode")
    @classmethod
    def _check_admin_mode(cls, value: str) -> str:
        if value not in _ADMIN_GROUP_MODES:
            raise ValueError(
                f"Unknown admin_group_mode '{value}'. Allowed: {sorted(_ADMIN_GROUP_MODES)}"
            )
        return value


class UpdateIdentityProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    issuer_url: str | None = Field(default=None, min_length=8, max_length=512)
    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    client_secret: str | None = Field(default=None, max_length=2048)
    scopes: list[str] | None = None
    response_mode: str | None = None
    groups_claim: str | None = Field(default=None, max_length=128)
    domain_allowlist: list[str] | None = None
    auto_provision: bool | None = None
    auto_link_on_verified_email: bool | None = None
    admin_group_mode: str | None = None
    admin_groups: list[str] | None = None
    enabled: bool | None = None

    @field_validator("response_mode")
    @classmethod
    def _check_response_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in _RESPONSE_MODES:
            raise ValueError(
                f"Unknown response_mode '{value}'. Allowed: {sorted(_RESPONSE_MODES)}"
            )
        return value

    @field_validator("admin_group_mode")
    @classmethod
    def _check_admin_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in _ADMIN_GROUP_MODES:
            raise ValueError(
                f"Unknown admin_group_mode '{value}'. Allowed: {sorted(_ADMIN_GROUP_MODES)}"
            )
        return value


def _to_view(provider: IdentityProvider) -> IdentityProviderView:
    return IdentityProviderView(
        id=provider.id,
        slug=provider.slug,
        display_name=provider.display_name,
        kind=provider.kind,
        issuer_url=provider.issuer_url,
        client_id=provider.client_id,
        has_client_secret=bool(provider.client_secret_encrypted),
        scopes=list(provider.scopes),
        response_mode=provider.response_mode,
        groups_claim=provider.groups_claim,
        domain_allowlist=list(provider.domain_allowlist or []) or None,
        auto_provision=provider.auto_provision,
        auto_link_on_verified_email=provider.auto_link_on_verified_email,
        admin_group_mode=provider.admin_group_mode,
        admin_groups=list(provider.admin_groups or []) or None,
        enabled=provider.enabled,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("", response_model=IdentityProviderListResponse)
async def list_identity_providers(
    session: AsyncSession = Depends(get_db_session),
    current: User = Depends(require_admin_or_owner),
) -> IdentityProviderListResponse:
    del current
    rows = (
        await session.scalars(
            select(IdentityProvider).order_by(IdentityProvider.created_at.asc())
        )
    ).all()
    return IdentityProviderListResponse(providers=[_to_view(row) for row in rows])


@router.post(
    "",
    response_model=IdentityProviderView,
    status_code=status.HTTP_201_CREATED,
)
async def create_identity_provider(
    payload: CreateIdentityProviderRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> IdentityProviderView:
    del _csrf

    cipher = OIDCSecretCipher(settings)
    encrypted: str | None = None
    if payload.client_secret:
        encrypted = cipher.encrypt(payload.client_secret)

    provider = IdentityProvider(
        slug=payload.slug,
        display_name=payload.display_name,
        kind=payload.kind,
        issuer_url=payload.issuer_url.rstrip("/"),
        client_id=payload.client_id,
        client_secret_encrypted=encrypted,
        scopes=payload.scopes,
        response_mode=payload.response_mode,
        groups_claim=payload.groups_claim,
        domain_allowlist=payload.domain_allowlist,
        auto_provision=payload.auto_provision,
        auto_link_on_verified_email=payload.auto_link_on_verified_email,
        admin_group_mode=payload.admin_group_mode,
        admin_groups=payload.admin_groups,
        enabled=payload.enabled,
    )
    session.add(provider)
    try:
        await session.flush()
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=owner.id,
                target_user_id=None,
                event_type="oidc_provider_created",
                metadata={"slug": provider.slug, "issuer_url": provider.issuer_url},
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "IDP_SLUG_TAKEN",
            "An identity provider with this slug already exists.",
        ) from exc
    await session.refresh(provider)
    return _to_view(provider)


@router.patch("/{provider_id}", response_model=IdentityProviderView)
async def update_identity_provider(
    provider_id: UUID,
    payload: UpdateIdentityProviderRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> IdentityProviderView:
    del _csrf

    provider = await session.get(IdentityProvider, provider_id)
    if provider is None:
        raise ApiError(404, "IDP_NOT_FOUND", "Identity provider not found")

    changed: dict[str, object] = {}

    for field in (
        "display_name",
        "issuer_url",
        "client_id",
        "scopes",
        "response_mode",
        "groups_claim",
        "domain_allowlist",
        "auto_provision",
        "auto_link_on_verified_email",
        "admin_group_mode",
        "admin_groups",
        "enabled",
    ):
        value = getattr(payload, field)
        if value is None:
            continue
        if field == "issuer_url" and isinstance(value, str):
            value = value.rstrip("/")
        if getattr(provider, field) != value:
            setattr(provider, field, value)
            changed[field] = value

    if payload.client_secret is not None:
        cipher = OIDCSecretCipher(settings)
        provider.client_secret_encrypted = cipher.encrypt(payload.client_secret)
        changed["client_secret"] = "<rotated>"

    provider.updated_at = datetime.now(UTC)

    if changed:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=owner.id,
                target_user_id=None,
                event_type="oidc_provider_updated",
                metadata={"slug": provider.slug, "fields": list(changed.keys())},
            ),
        )

    await session.commit()
    await session.refresh(provider)
    return _to_view(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_provider(
    provider_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    provider = await session.get(IdentityProvider, provider_id)
    if provider is None:
        raise ApiError(404, "IDP_NOT_FOUND", "Identity provider not found")

    in_use = await session.scalar(
        select(UserIdentity.id).where(UserIdentity.provider_id == provider.id).limit(1)
    )
    if in_use is not None:
        raise ApiError(
            409,
            "IDP_IN_USE",
            "This identity provider is linked to existing users. "
            "Disable it instead, or unlink users first.",
        )

    # Soft-revoke any active SCIM clients tied to this provider before
    # the FK SET NULL fires — preserves audit lineage with reason.
    from backend.app.models.scim_client import SCIMClient

    revoked_at = datetime.now(UTC)
    await session.execute(
        SCIMClient.__table__.update()
        .where(
            SCIMClient.provider_id == provider.id,
            SCIMClient.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at, revoked_reason="provider_deleted")
    )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="oidc_provider_deleted",
            metadata={"slug": provider.slug},
        ),
    )
    await session.delete(provider)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_id}/test", response_model=IdentityProviderTestResponse)
async def test_identity_provider(
    provider_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> IdentityProviderTestResponse:
    """Probe the IdP discovery + JWKS endpoints with the configured client.

    No tokens are minted — this is purely a connectivity check the owner
    can run before flipping `enabled=true`.
    """
    del _csrf, owner

    provider = await session.get(IdentityProvider, provider_id)
    if provider is None:
        raise ApiError(404, "IDP_NOT_FOUND", "Identity provider not found")

    cipher = OIDCSecretCipher(settings)
    secret = (
        cipher.decrypt(provider.client_secret_encrypted)
        if provider.client_secret_encrypted
        else None
    )
    client = OIDCClient(
        issuer_url=provider.issuer_url,
        client_id=provider.client_id,
        client_secret=secret,
        scopes=list(provider.scopes),
    )
    issuer_ok = False
    jwks_ok = False
    auth_endpoint: str | None = None
    token_endpoint: str | None = None
    keys = 0
    error: str | None = None
    try:
        try:
            doc = await client.discovery()
            issuer_ok = True
            auth_endpoint = doc.authorization_endpoint
            token_endpoint = doc.token_endpoint
        except ApiError as exc:
            error = exc.code
        if issuer_ok:
            try:
                jwks = await client.jwks(force_refresh=True)
                jwks_ok = True
                keys = len(jwks.get("keys", []))
            except ApiError as exc:
                error = exc.code
    finally:
        await client.aclose()

    return IdentityProviderTestResponse(
        issuer_ok=issuer_ok,
        jwks_ok=jwks_ok,
        issuer_url=provider.issuer_url,
        authorization_endpoint=auth_endpoint,
        token_endpoint=token_endpoint,
        jwks_keys=keys,
        error=error,
    )
