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
    provider = _make_provider()
    db_session.add(provider)
    await db_session.commit()

    claims = _make_claims(email_verified=False)
    with pytest.raises(ApiError) as exc:
        await find_or_create_user(db_session, provider=provider, claims=claims)
    assert exc.value.code == "OIDC_EMAIL_UNVERIFIED"


async def test_find_or_create_user_enforces_domain_allowlist(db_session):
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


async def test_admin_create_provider_owner_only(client, db_session, settings):
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
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_OWNER_ONLY"


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
