"""OIDC provider config + state lifecycle + provisioning tests (Phase 30.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.app.api.auth_oidc import _safe_return_to, prune_expired_states
from backend.app.auth.oidc_cipher import OIDCSecretCipher
from backend.app.auth.oidc_client import (
    IdTokenClaims,
    generate_pkce,
    generate_state,
    hash_state,
)
from backend.app.auth.oidc_provisioning import (
    find_or_create_user,
    link_existing_user,
)
from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.oidc_login_state import OIDCLoginState
from backend.app.models.user import User
from backend.app.models.user_identity import UserIdentity


_TEST_CSRF = "csrf-token"


async def _authenticate(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _TEST_CSRF}


async def _make_owner(db_session) -> User:
    user = User(
        email="owner@example.com",
        password_hash=hash_password("password-1234"),
        name="Owner",
        role=UserRole.OWNER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_user(
    db_session,
    *,
    email: str = "user@example.com",
    role: UserRole = UserRole.USER,
    auth_source: str = "password",
    password: str | None = "password-1234",
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password) if password else None,
        name=None,
        role=role,
        auth_source=auth_source,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _make_provider(**overrides) -> IdentityProvider:
    base = {
        "slug": "okta-prod",
        "display_name": "Continue with Okta",
        "kind": "oidc",
        "issuer_url": "https://example.okta.com",
        "client_id": "test-client",
        "scopes": ["openid", "profile", "email"],
        "response_mode": "query",
        "auto_provision": True,
        "admin_group_mode": "ignore",
        "enabled": True,
    }
    base.update(overrides)
    return IdentityProvider(**base)


def _make_claims(**overrides) -> IdTokenClaims:
    base = {
        "sub": "okta-sub-1",
        "iss": "https://example.okta.com",
        "aud": "test-client",
        "email": "newhire@example.com",
        "email_verified": True,
        "name": "New Hire",
        "groups": [],
        "raw": {},
    }
    base.update(overrides)
    return IdTokenClaims(**base)


# ---------------------------------------------------------------------------
# OIDCSecretCipher
# ---------------------------------------------------------------------------


def test_oidc_cipher_round_trips(settings):
    cipher = OIDCSecretCipher(settings)
    secret = "okta-client-secret-very-long-AAAAA"
    blob = cipher.encrypt(secret)
    assert blob != secret
    assert cipher.decrypt(blob) == secret


def test_oidc_cipher_domain_separated(settings):
    """SecretCipher and OIDCSecretCipher must not interchange."""
    from backend.app.admin.secret_service import SecretCipher

    oidc = OIDCSecretCipher(settings)
    llm = SecretCipher(settings)
    blob = oidc.encrypt("hello")
    with pytest.raises(ApiError):
        llm.decrypt(blob)


# ---------------------------------------------------------------------------
# CRIT-03: independent encryption secrets (opt-in, JWT-fallback by default)
# ---------------------------------------------------------------------------


def _settings_with_independent_secrets(
    base_settings, *, llm: str | None = None, oidc: str | None = None
):
    """Return a Settings copy with the new independent encryption fields set."""
    from pydantic import SecretStr

    auth = base_settings.auth.model_copy(
        update={
            "llm_encryption_secret": SecretStr(llm) if llm is not None else None,
            "oidc_encryption_secret": SecretStr(oidc) if oidc is not None else None,
        }
    )
    return base_settings.model_copy(update={"auth": auth})


def test_llm_cipher_uses_jwt_fallback_when_independent_secret_unset(settings):
    """Default state: no behavior change — uses JWT-derived key."""
    from backend.app.admin.secret_service import SecretCipher

    cipher = SecretCipher(settings)
    assert cipher.uses_independent_secret is False
    blob = cipher.encrypt("openai-key")
    assert cipher.decrypt(blob) == "openai-key"


def test_llm_cipher_with_independent_secret_round_trips(settings):
    """Opt-in mode: dedicated secret produces a usable Fernet key."""
    from backend.app.admin.secret_service import SecretCipher

    s = _settings_with_independent_secrets(settings, llm="llm-only-secret-32-bytes-min!!!")
    cipher = SecretCipher(s)
    assert cipher.uses_independent_secret is True
    blob = cipher.encrypt("openai-key")
    assert cipher.decrypt(blob) == "openai-key"


def test_llm_cipher_independent_secret_decouples_from_jwt(settings):
    """Knowing jwt_secret must not let an attacker decrypt rows written
    under the independent llm_encryption_secret."""
    from backend.app.admin.secret_service import SecretCipher

    indep = _settings_with_independent_secrets(settings, llm="llm-only-secret-32-bytes-min!!!")
    cipher_indep = SecretCipher(indep)
    blob = cipher_indep.encrypt("openai-key")

    # `settings` has the independent secret unset, so its cipher uses the
    # legacy jwt-derived key. It must NOT decrypt rows from the
    # independent-secret deployment.
    cipher_legacy = SecretCipher(settings)
    from cryptography.fernet import InvalidToken

    with pytest.raises((InvalidToken, ApiError)):
        cipher_legacy.decrypt(blob)


def test_oidc_cipher_uses_jwt_fallback_when_independent_secret_unset(settings):
    cipher = OIDCSecretCipher(settings)
    assert cipher.uses_independent_secret is False
    blob = cipher.encrypt("idp-client-secret")
    assert cipher.decrypt(blob) == "idp-client-secret"


def test_oidc_cipher_with_independent_secret_round_trips(settings):
    s = _settings_with_independent_secrets(settings, oidc="oidc-only-secret-32-bytes-min!!!")
    cipher = OIDCSecretCipher(s)
    assert cipher.uses_independent_secret is True
    blob = cipher.encrypt("idp-client-secret")
    assert cipher.decrypt(blob) == "idp-client-secret"


def test_oidc_cipher_independent_secret_decouples_from_jwt(settings):
    indep = _settings_with_independent_secrets(settings, oidc="oidc-only-secret-32-bytes-min!!!")
    cipher_indep = OIDCSecretCipher(indep)
    blob = cipher_indep.encrypt("idp-client-secret")

    cipher_legacy = OIDCSecretCipher(settings)
    from cryptography.fernet import InvalidToken

    with pytest.raises((InvalidToken, ApiError)):
        cipher_legacy.decrypt(blob)


def test_independent_llm_and_oidc_secrets_remain_domain_separated(settings):
    """Independent mode: LLM and OIDC ciphers configured with different
    secrets must not be able to decrypt each other's blobs."""
    from backend.app.admin.secret_service import SecretCipher
    from cryptography.fernet import InvalidToken

    s = _settings_with_independent_secrets(
        settings,
        llm="llm-only-secret-32-bytes-min!!!",
        oidc="oidc-only-secret-32-bytes-min!!!",
    )
    llm = SecretCipher(s)
    oidc = OIDCSecretCipher(s)

    llm_blob = llm.encrypt("llm-key")
    oidc_blob = oidc.encrypt("oidc-key")

    with pytest.raises((InvalidToken, ApiError)):
        oidc.decrypt(llm_blob)
    with pytest.raises((InvalidToken, ApiError)):
        llm.decrypt(oidc_blob)


# ---------------------------------------------------------------------------
# Helpers — PKCE / state / return_to sanitisation
# ---------------------------------------------------------------------------


def test_pkce_generates_distinct_pairs():
    pairs = {generate_pkce() for _ in range(50)}
    assert len(pairs) == 50
    for verifier, challenge in pairs:
        assert 43 <= len(verifier) <= 128
        # base64url-no-padding challenge
        assert "=" not in challenge


def test_state_hash_is_stable():
    state = generate_state()
    assert hash_state(state) == hash_state(state)
    assert hash_state(state) != hash_state(generate_state())


def test_safe_return_to_rejects_open_redirects(settings):
    class _Req:
        base_url = "http://localhost/"

    cases = {
        None: "/",
        "": "/",
        "/repos/example": "/repos/example",
        "/repos?x=1": "/repos?x=1",
        "//evil.example.com/x": "/",
        "https://evil.example.com/x": "/",
        "javascript:alert(1)": "/",
    }
    for raw, expected in cases.items():
        assert _safe_return_to(_Req(), settings, raw) == expected


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def test_find_or_create_user_uses_existing_identity(db_session):
    user = await _make_user(db_session, auth_source="oidc", password=None)
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)
    db_session.add(
        UserIdentity(
            user_id=user.id,
            provider_id=provider.id,
            subject="okta-sub-1",
            email_at_link=user.email,
        )
    )
    await db_session.commit()

    claims = _make_claims(sub="okta-sub-1", email=user.email)
    resolved = await find_or_create_user(db_session, provider=provider, claims=claims)
    assert resolved.id == user.id


async def test_find_or_create_user_refuses_email_collision(db_session):
    local = await _make_user(db_session, email="dup@example.com")
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="dup@example.com")
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_LINK_REQUIRED"
    # Local user untouched.
    fresh = await db_session.get(User, local.id)
    assert fresh is not None
    assert fresh.auth_source == "password"


async def test_find_or_create_user_blocks_unverified_email(db_session):
    # With NO domain_allowlist there is no admin-supplied trust anchor
    # to substitute for the IdP's email_verified flag, so we still
    # refuse to provision when the claim is missing/false. This is the
    # multi-tenant / wide-open default — a verified email is the only
    # signal that the inbox is actually controlled.
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email_verified=False)
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_EMAIL_UNVERIFIED"


async def test_find_or_create_user_provisions_when_domain_allowlist_substitutes_for_email_verified(
    db_session,
):
    # The Okta unblock: Okta omits `email_verified` from the ID token by
    # default. When an admin has explicitly pinned the trusted domains
    # via `domain_allowlist` and the email's domain matches, treat the
    # allowlist match itself as the admin-supplied trust anchor and
    # provision the new account. Symmetric to the auto-link path.
    provider = _make_provider(domain_allowlist=["finteqhub.com"])
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(
        email="newhire@finteqhub.com",
        email_verified=False,
    )
    user = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()
    assert user.email == "newhire@finteqhub.com"
    assert user.auth_source == "oidc"
    assert user.password_hash is None


async def test_find_or_create_user_still_rejects_when_allowlist_set_but_domain_mismatch(
    db_session,
):
    # Edge case: allowlist is set AND email_verified=false AND the email
    # domain is outside the allowlist. We must NOT silently fall back to
    # verifying via email_verified=false (which would be "trusted" only
    # via the new allowlist path). The cross-tenant attack vector this
    # closes: a misconfigured wildcard IdP returning an outside-domain
    # email with no email_verified claim must not provision an account.
    provider = _make_provider(domain_allowlist=["finteqhub.com"])
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(
        email="alien@other.com",
        email_verified=False,
    )
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_EMAIL_UNVERIFIED"


async def test_find_or_create_user_enforces_domain_allowlist(db_session):
    # When `email_verified=true` carries the trust signal, the separate
    # domain-allowlist check still has to fire — we don't want a
    # different-tenant verified email to provision into this tenant.
    # This surfaces the clearer OIDC_DOMAIN_NOT_ALLOWED code rather
    # than the ambiguous OIDC_EMAIL_UNVERIFIED one.
    provider = _make_provider(domain_allowlist=["allowed.com"])
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="alien@blocked.com")
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_DOMAIN_NOT_ALLOWED"


async def test_find_or_create_user_provisions_with_user_role_by_default(db_session):
    provider = _make_provider(admin_group_mode="ignore")
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(groups=["admins", "engineering"])
    user = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()
    assert user.role is UserRole.USER
    assert user.auth_source == "oidc"
    assert user.password_hash is None


async def test_owner_delegated_promotes_when_groups_intersect(db_session):
    provider = _make_provider(
        admin_group_mode="owner_delegated",
        admin_groups=["admins"],
    )
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(groups=["admins", "engineering"])
    user = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()
    assert user.role is UserRole.ADMIN


async def test_owner_delegated_does_not_promote_without_intersection(db_session):
    provider = _make_provider(
        admin_group_mode="owner_delegated",
        admin_groups=["admins"],
    )
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(groups=["engineering"])
    user = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()
    assert user.role is UserRole.USER


async def test_auto_link_attaches_existing_user_and_clears_password(db_session):
    from sqlalchemy import select

    local = await _make_user(db_session, email="bootstrap@example.com")
    assert local.password_hash is not None
    provider = _make_provider(auto_link_on_verified_email=True)
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)

    claims = _make_claims(email="bootstrap@example.com")
    resolved = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()

    assert resolved.id == local.id
    fresh = await db_session.get(User, local.id)
    assert fresh is not None
    assert fresh.password_hash is None
    assert fresh.auth_source == "oidc"
    identity = await db_session.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == local.id,
            UserIdentity.provider_id == provider.id,
        )
    )
    assert identity is not None
    assert identity.subject == "okta-sub-1"


async def test_auto_link_disabled_still_refuses_collision(db_session):
    local = await _make_user(db_session, email="dup@example.com")
    provider = _make_provider(auto_link_on_verified_email=False)
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="dup@example.com")
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_LINK_REQUIRED"
    fresh = await db_session.get(User, local.id)
    assert fresh is not None
    assert fresh.password_hash is not None
    assert fresh.auth_source == "password"


async def test_auto_link_requires_email_verified_without_allowlist(db_session):
    # No domain_allowlist on the provider → the IdP-asserted email_verified
    # flag is still required. This is the wide-open / multi-tenant case.
    await _make_user(db_session, email="dup@example.com")
    provider = _make_provider(auto_link_on_verified_email=True)
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="dup@example.com", email_verified=False)
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_LINK_REQUIRED"


async def test_auto_link_trusts_domain_allowlist_without_email_verified(db_session):
    # Admin pinned domain_allowlist → the allowlist match itself is the trust
    # signal; the IdP's email_verified flag is not required. Mirrors the prod
    # Okta deployment where Okta omits email_verified by default.
    from sqlalchemy import select

    local = await _make_user(db_session, email="user@allowed.com")
    provider = _make_provider(
        auto_link_on_verified_email=True,
        domain_allowlist=["allowed.com"],
    )
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)

    claims = _make_claims(email="user@allowed.com", email_verified=False)
    resolved = await find_or_create_user(db_session, provider=provider, claims=claims)
    await db_session.commit()

    assert resolved.id == local.id
    fresh = await db_session.get(User, local.id)
    assert fresh is not None
    assert fresh.password_hash is None
    assert fresh.auth_source == "oidc"
    identity = await db_session.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == local.id,
            UserIdentity.provider_id == provider.id,
        )
    )
    assert identity is not None
    assert identity.subject == "okta-sub-1"


async def test_auto_link_enforces_domain_allowlist(db_session):
    await _make_user(db_session, email="alien@blocked.com")
    provider = _make_provider(
        auto_link_on_verified_email=True,
        domain_allowlist=["allowed.com"],
    )
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="alien@blocked.com")
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_LINK_REQUIRED"


async def test_auto_link_refuses_disabled_user(db_session):
    local = await _make_user(db_session, email="dup@example.com")
    local.is_active = False
    await db_session.commit()
    provider = _make_provider(auto_link_on_verified_email=True)
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email="dup@example.com")
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "ACCOUNT_DISABLED"


async def test_link_existing_user_attaches_identity(db_session):
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)
    user = await _make_user(db_session, email="local@example.com")

    claims = _make_claims(sub="okta-sub-9", email="local@example.com")
    identity = await link_existing_user(
        db_session,
        user_id=user.id,
        provider=provider,
        claims=claims,
    )
    await db_session.commit()
    assert identity.user_id == user.id
    assert identity.subject == "okta-sub-9"


async def test_link_existing_refuses_when_email_belongs_to_other(db_session):
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()
    me = await _make_user(db_session, email="me@example.com")
    other = await _make_user(db_session, email="other@example.com")

    claims = _make_claims(sub="ok-1", email=other.email)
    with pytest.raises(ApiError) as exc:
        await link_existing_user(
            db_session,
            user_id=me.id,
            provider=provider,
            claims=claims,
        )
    assert exc.value.code == "OIDC_LINK_EMAIL_BELONGS_TO_OTHER"


# ---------------------------------------------------------------------------
# State lifecycle
# ---------------------------------------------------------------------------


async def test_prune_expired_states_drops_only_old_rows(db_session):
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)

    fresh = OIDCLoginState(
        state_hash=hash_state("a" * 32),
        provider_id=provider.id,
        code_verifier="v",
        nonce="n",
        return_to="/",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    stale = OIDCLoginState(
        state_hash=hash_state("b" * 32),
        provider_id=provider.id,
        code_verifier="v",
        nonce="n",
        return_to="/",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add_all([fresh, stale])
    await db_session.commit()

    pruned = await prune_expired_states(db_session)
    assert pruned == 1
    db_session.expire_all()
    from sqlalchemy import select

    rows = (await db_session.scalars(select(OIDCLoginState))).all()
    assert len(rows) == 1
    assert rows[0].state_hash == fresh.state_hash


# ---------------------------------------------------------------------------
# Login dance — partial — authorize redirect / disabled provider
# ---------------------------------------------------------------------------


async def test_login_redirects_to_authorize_endpoint(client, db_session, settings):
    provider = _make_provider(slug="okta-test")
    db_session.add(provider)
    await db_session.commit()
    provider_id = provider.id

    fake = type(
        "Doc",
        (),
        {
            "issuer": "https://example.okta.com",
            "authorization_endpoint": "https://example.okta.com/oauth2/v1/authorize",
            "token_endpoint": "https://example.okta.com/oauth2/v1/token",
            "jwks_uri": "https://example.okta.com/oauth2/v1/keys",
            "end_session_endpoint": None,
            "userinfo_endpoint": None,
        },
    )()

    with patch(
        "backend.app.auth.oidc_client.OIDCClient.discovery",
        return_value=fake,
    ):
        response = await client.get(
            "/api/auth/oidc/okta-test/login?return_to=/repos",
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://example.okta.com/oauth2/v1/authorize?")
    assert "response_type=code" in location
    assert "client_id=test-client" in location
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "scope=openid+profile+email" in location

    # State row stored.
    db_session.expire_all()
    from sqlalchemy import select

    rows = (await db_session.scalars(select(OIDCLoginState))).all()
    assert len(rows) == 1
    assert rows[0].provider_id == provider_id
    assert rows[0].return_to == "/repos"


async def test_login_returns_404_for_unknown_slug(client):
    response = await client.get(
        "/api/auth/oidc/missing/login",
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IDP_NOT_FOUND"


async def test_login_returns_410_when_disabled(client, db_session):
    provider = _make_provider(slug="paused", enabled=False)
    db_session.add(provider)
    await db_session.commit()

    response = await client.get(
        "/api/auth/oidc/paused/login",
        follow_redirects=False,
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "IDP_DISABLED"


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------


async def test_admin_can_create_provider(client, db_session, settings):
    """Admin and owner share one tier — admins can create identity providers."""
    user = await _make_user(db_session, email="admin@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, user)

    response = await client.post(
        "/api/admin/identity-providers",
        json={
            "slug": "okta-prod",
            "display_name": "Continue with Okta",
            "issuer_url": "https://example.okta.com",
            "client_id": "test-client",
            "client_secret": "very-secret-value",
        },
        headers=_csrf_headers(),
    )
    assert response.status_code == 201


async def test_admin_create_provider_owner_persists_and_hides_secret(
    client, db_session, settings
):
    owner = await _make_owner(db_session)
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/identity-providers",
        json={
            "slug": "okta-prod",
            "display_name": "Continue with Okta",
            "issuer_url": "https://example.okta.com",
            "client_id": "test-client",
            "client_secret": "very-secret-value",
        },
        headers=_csrf_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "okta-prod"
    assert body["has_client_secret"] is True
    assert "client_secret" not in body


async def test_auth_config_includes_enabled_oidc_providers(client, db_session):
    db_session.add(_make_provider(slug="okta-prod", display_name="Continue with Okta"))
    db_session.add(
        _make_provider(
            slug="azure-eu",
            display_name="Continue with Azure",
            issuer_url="https://login.microsoftonline.com/tenant",
            enabled=False,
        )
    )
    await db_session.commit()

    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    providers = response.json()["providers"]
    kinds = [p["kind"] for p in providers]
    assert "password" in kinds
    oidc = [p for p in providers if p["kind"] == "oidc"]
    assert len(oidc) == 1
    assert oidc[0]["slug"] == "okta-prod"
    assert oidc[0]["login_url"] == "/api/auth/oidc/okta-prod/login"
