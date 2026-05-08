"""Mapping IdP-asserted claims to a Cograph user.

Provisioning policy:

1. Existing `(provider_id, sub)` identity -> that user.
2. Email collision with a local user -> refuse with `OIDC_LINK_REQUIRED`.
   *Never auto-link.* The user must sign in with the existing credential
   and start a `link/{slug}/start` flow from their authenticated session.
3. Auto-provision a new user iff the provider opts in:
   - `auto_provision=true`
   - `email_verified=true` in the ID token
   - `email` domain inside `domain_allowlist` (when set)
4. Group to admin promotion is OFF unless `admin_group_mode='owner_delegated'`
   AND the IdP-asserted groups intersect `admin_groups`. Group removal on
   subsequent login NEVER demotes - owner action required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.oidc_client import IdTokenClaims
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.user import User
from backend.app.models.user_identity import UserIdentity


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower()


async def find_or_create_user(
    session: AsyncSession,
    *,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> User:
    """Resolve `claims.sub` to a Cograph user, creating one if allowed.

    Raises `ApiError` for the structured failure modes the FE renders.
    Caller is responsible for committing the surrounding transaction.
    """
    # 1. Existing identity → reuse the linked user.
    existing_identity = await session.scalar(
        select(UserIdentity)
        .options(selectinload(UserIdentity.user))
        .where(
            UserIdentity.provider_id == provider.id,
            UserIdentity.subject == claims.sub,
        )
    )
    if existing_identity is not None:
        user = existing_identity.user
        if not user.is_active:
            raise ApiError(
                403,
                "ACCOUNT_DISABLED",
                "Account is disabled. Contact an administrator.",
            )
        existing_identity.last_login_at = datetime.now(UTC)
        return user

    # 2. Email collision — refuse to auto-link.
    if claims.email:
        existing_user = await session.scalar(
            select(User).where(User.email == claims.email)
        )
        if existing_user is not None:
            raise ApiError(
                409,
                "OIDC_LINK_REQUIRED",
                "An account with this email already exists. Sign in with "
                "your password and link this provider from your account "
                "settings.",
            )

    # 3. Auto-provision gates.
    if not provider.auto_provision:
        raise ApiError(
            403,
            "OIDC_AUTO_PROVISION_DISABLED",
            "This identity provider does not auto-create accounts. "
            "Ask an administrator to add you first.",
        )
    if not claims.email:
        raise ApiError(
            400,
            "OIDC_EMAIL_MISSING",
            "Identity provider did not return an email claim",
        )
    if not claims.email_verified:
        raise ApiError(
            403,
            "OIDC_EMAIL_UNVERIFIED",
            "Identity provider has not verified this email",
        )
    domain = _email_domain(claims.email)
    if provider.domain_allowlist:
        if domain is None or domain not in {d.lower() for d in provider.domain_allowlist}:
            raise ApiError(
                403,
                "OIDC_DOMAIN_NOT_ALLOWED",
                "Email domain is not permitted for this identity provider",
            )

    role = _resolve_initial_role(provider=provider, claims=claims)

    user = User(
        email=claims.email,
        password_hash=None,
        name=claims.name,
        role=role,
        auth_source="oidc",
    )
    session.add(user)
    await session.flush()

    identity = UserIdentity(
        user_id=user.id,
        provider_id=provider.id,
        subject=claims.sub,
        email_at_link=claims.email,
        last_login_at=datetime.now(UTC),
    )
    session.add(identity)

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=None,
            target_user_id=user.id,
            event_type="user_provisioned_oidc",
            metadata={
                "provider_slug": provider.slug,
                "provider_id": str(provider.id),
                "subject": claims.sub,
                "role": role.value,
            },
        ),
    )
    return user


def _resolve_initial_role(
    *,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> UserRole:
    """Decide the role for a freshly auto-provisioned user.

    OWNER is a bootstrap-only label and is never assigned via OIDC.
    """
    if provider.admin_group_mode != "owner_delegated":
        return UserRole.USER
    if not provider.admin_groups:
        return UserRole.USER
    if not claims.groups:
        return UserRole.USER
    if any(group in provider.admin_groups for group in claims.groups):
        return UserRole.ADMIN
    return UserRole.USER


async def link_existing_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> UserIdentity:
    """Link a `(provider_id, sub)` pair to an already-authenticated user.

    Used by the `/api/me/identities/link/{slug}/start` flow so a
    local-password account can add OIDC without auto-link risk.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "User not found")
    if not user.is_active:
        raise ApiError(403, "ACCOUNT_DISABLED", "Account is disabled")

    # If the IdP-asserted email belongs to a *different* local user,
    # refuse to silently move the link.
    if claims.email:
        existing_user = await session.scalar(
            select(User).where(User.email == claims.email)
        )
        if existing_user is not None and existing_user.id != user.id:
            raise ApiError(
                409,
                "OIDC_LINK_EMAIL_BELONGS_TO_OTHER",
                "Identity provider returned an email that belongs to a "
                "different Cograph account.",
            )

    # If a different local user is already linked to this `(provider, sub)`,
    # refuse — the IdP `sub` belongs to that account.
    other_link = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider_id == provider.id,
            UserIdentity.subject == claims.sub,
            UserIdentity.user_id != user.id,
        )
    )
    if other_link is not None:
        raise ApiError(
            409,
            "OIDC_LINK_TAKEN",
            "This identity is already linked to a different account.",
        )

    # If the same `(provider, sub)` is already linked to this user, return it.
    own_link = await session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider_id == provider.id,
            UserIdentity.subject == claims.sub,
            UserIdentity.user_id == user.id,
        )
    )
    if own_link is not None:
        own_link.last_login_at = datetime.now(UTC)
        return own_link

    identity = UserIdentity(
        user_id=user.id,
        provider_id=provider.id,
        subject=claims.sub,
        email_at_link=claims.email,
        last_login_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=user.id,
            target_user_id=user.id,
            event_type="user_identity_linked",
            metadata={
                "provider_slug": provider.slug,
                "provider_id": str(provider.id),
                "subject": claims.sub,
            },
        ),
    )
    return identity
