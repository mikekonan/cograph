"""Mapping IdP-asserted claims to a Cograph user.

Provisioning policy:

1. Existing `(provider_id, sub)` identity -> that user.
2. Email collision with a local user -> by default, refuse with
   `OIDC_LINK_REQUIRED` so the user has to sign in with the existing
   credential and start a `link/{slug}/start` flow from their
   authenticated session.
   If the provider has `auto_link_on_verified_email=true` AND the
   email's trustworthiness is established by *either* of:
     - the IdP asserting `email_verified=true`, *or*
     - the provider having a non-empty `domain_allowlist` that the
       email's domain matches (the admin pre-trusts that domain),
   then the existing local user is auto-linked. As part of the link the
   local password is cleared (`password_hash=None`, `auth_source='oidc'`)
   so the account becomes SSO-only — this is the "SSO supersedes
   password" migration path.
3. Auto-provision a new user iff the provider opts in:
   - `auto_provision=true`
   - `email` is present in the ID token
   - the email is "trusted", by EITHER
       - the IdP asserting `email_verified=true`, OR
       - the provider having a non-empty `domain_allowlist` that the
         email's domain matches (the admin pre-trusts that domain).
     The same rationale as for auto-link applies: when an admin has
     explicitly pinned the trusted domains, the allowlist match is a
     stronger signal than the IdP's self-asserted `email_verified`
     flag (Okta and several other IdPs omit it by default).
   - when `domain_allowlist` is set, the email's domain must match it
     regardless of which trust signal carried us here.
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
from backend.app.auth.oidc_group_sync import sync_oidc_group_memberships
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.user import User
from backend.app.models.user_identity import UserIdentity


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower()


def _email_is_trusted(
    *,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> bool:
    """Whether we can trust the email claim well enough to use it for
    account-creation/account-linking decisions.

    Two equally strong signals; EITHER is sufficient:

      1. The IdP asserts `email_verified=true` in the ID token (the
         RFC-spec'd signal).
      2. The provider has a non-empty `domain_allowlist` AND the email's
         domain matches one of the allowlisted domains. An admin
         explicitly pinning trusted domains is a stronger signal than
         the IdP's self-asserted flag — and crucially, this path is
         what unblocks Okta, which by default does not emit
         `email_verified` without custom authorization-server config.

    When neither holds (no `email_verified`, no allowlist match), the
    email cannot be trusted as proof of control over the inbox.

    Note: this function does NOT enforce the domain allowlist on its
    own. Callers must still run the separate domain-allowlist check
    (`OIDC_DOMAIN_NOT_ALLOWED`) to surface a clear "wrong tenant"
    error instead of silently rejecting via `OIDC_EMAIL_UNVERIFIED`.
    """
    if not claims.email:
        return False
    if claims.email_verified:
        return True
    allowlist = {d.lower() for d in (provider.domain_allowlist or [])}
    if not allowlist:
        return False
    domain = _email_domain(claims.email)
    return domain is not None and domain in allowlist


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
        await sync_oidc_group_memberships(
            session=session,
            user_id=user.id,
            provider=provider,
            claim_groups=claims.groups,
        )
        return user

    # 2. Email collision — either auto-link (opt-in) or refuse.
    if claims.email:
        existing_user = await session.scalar(
            select(User).where(User.email == claims.email)
        )
        if existing_user is not None:
            if _can_auto_link(provider=provider, claims=claims):
                linked_user = await _auto_link_existing_user(
                    session,
                    user=existing_user,
                    provider=provider,
                    claims=claims,
                )
                await sync_oidc_group_memberships(
                    session=session,
                    user_id=linked_user.id,
                    provider=provider,
                    claim_groups=claims.groups,
                )
                return linked_user
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
    # `email_verified=true` is no longer required unconditionally; we
    # accept the admin-pinned domain allowlist as an equally strong
    # trust anchor (see `_email_is_trusted`). Okta in particular omits
    # this claim by default, so requiring it broke every fresh-user
    # SSO sign-up on Okta-backed deployments.
    if not _email_is_trusted(provider=provider, claims=claims):
        raise ApiError(
            403,
            "OIDC_EMAIL_UNVERIFIED",
            "Identity provider has not verified this email",
        )
    # Even when the email is trusted via the allowlist path, we still
    # reject if the provider has an allowlist set AND the email's
    # domain does not match it. This handles the edge case where
    # `email_verified=true` is asserted but the domain is outside the
    # tenant — without this we'd happily create cross-tenant accounts.
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
    await sync_oidc_group_memberships(
        session=session,
        user_id=user.id,
        provider=provider,
        claim_groups=claims.groups,
    )
    return user


def _can_auto_link(
    *,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> bool:
    """Decide whether a colliding email may be auto-linked to the local user.

    Three gates must hold:

      1. Provider opted in (`auto_link_on_verified_email=true`).
      2. The email claim is trusted (see `_email_is_trusted` — accepts
         either `email_verified=true` or a domain_allowlist match).
      3. When `domain_allowlist` is set, the email's domain matches it.
         This is the tenant-scope guard: a verified email from a
         different tenant must not be silently linked into this one.
         Symmetric to the separate OIDC_DOMAIN_NOT_ALLOWED check on
         the auto-provision path.
    """
    if not provider.auto_link_on_verified_email:
        return False
    if not _email_is_trusted(provider=provider, claims=claims):
        return False
    allowlist = {d.lower() for d in (provider.domain_allowlist or [])}
    if not allowlist:
        return True
    domain = _email_domain(claims.email)
    return domain is not None and domain in allowlist


async def _auto_link_existing_user(
    session: AsyncSession,
    *,
    user: User,
    provider: IdentityProvider,
    claims: IdTokenClaims,
) -> User:
    """Attach the IdP identity to `user`, then close the password path.

    Clearing `password_hash` and switching `auth_source` to `oidc` makes
    the account SSO-only — the bootstrap/legacy password no longer
    authenticates. This is the migration path for "we just turned SSO
    on for this tenant; existing local accounts should keep working
    via Okta and only Okta."
    """
    if not user.is_active:
        raise ApiError(
            403,
            "ACCOUNT_DISABLED",
            "Account is disabled. Contact an administrator.",
        )

    user.password_hash = None
    user.auth_source = "oidc"

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
            actor_user_id=None,
            target_user_id=user.id,
            event_type="user_identity_auto_linked",
            metadata={
                "provider_slug": provider.slug,
                "provider_id": str(provider.id),
                "subject": claims.sub,
                "password_cleared": True,
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
