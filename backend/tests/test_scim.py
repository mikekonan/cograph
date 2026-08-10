"""SCIM 2.0 deprovisioning tests (Phase 30.4).

Covers:
- Bearer-token resolver (valid / revoked / wrong scope / disabled IdP)
- POST /Users — provision new + idempotent on dup email
- PUT /Users/{id} — replace + active=false → cascade
- PATCH /Users/{id} — single-field updates
- DELETE /Users/{id} — soft deprovision (204)
- Owner protection — disable owner via SCIM is rejected and audited
- Idempotency — same payload replayed → 1 cascade, 4 dedupe
- Layer-1 propagation — disabled user's PAT 401s next request
- /Groups → 501
- Filter limits — userName eq works, attribute co fails 400 invalidFilter
- Admin CRUD — owner-only mint/rotate/revoke; admin can list
- Provider deletion — soft-revokes its SCIM clients (provider_deleted)
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.auth.scim_resolver import resolve_scim_client
from backend.app.core.auth import TokenType, create_token
from backend.app.core.deps import PAT_PLAINTEXT_PREFIX, PAT_PREFIX_DISPLAY_CHARS
from backend.app.models.audit_event import AuditEvent
from backend.app.models.enums import UserRole
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.scim_client import SCIMClient
from backend.app.models.scim_event import SCIMEvent
from backend.app.models.user import User
from backend.app.scim.payload import build_idempotency_key, canonical_payload_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode()).digest()


async def _make_provider(session, slug: str = "okta-scim") -> IdentityProvider:
    provider = IdentityProvider(
        slug=slug,
        display_name="Okta SCIM",
        kind="oidc",
        issuer_url="https://example.okta.com",
        client_id="0oamockclient",
        client_secret_encrypted=None,
        scopes=["openid", "profile", "email"],
        response_mode="query",
        admin_group_mode="ignore",
        enabled=True,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


async def _make_scim_client(
    session,
    *,
    provider: IdentityProvider,
    scopes: list[str] | None = None,
    revoked: bool = False,
) -> tuple[SCIMClient, str]:
    plaintext = f"{PAT_PLAINTEXT_PREFIX}{secrets.token_urlsafe(36)}"
    row = SCIMClient(
        provider_id=provider.id,
        name="Okta SCIM",
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:PAT_PREFIX_DISPLAY_CHARS],
        scopes=scopes or ["users:write"],
    )
    if revoked:
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = "admin"
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, plaintext


async def _make_user(
    session, *, email: str, role: UserRole = UserRole.USER, active: bool = True
) -> User:
    user = User(
        email=email,
        password_hash="dummy",
        name="Test User",
        role=role,
        is_active=active,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolver_accepts_active_token(db_session):
    provider = await _make_provider(db_session)
    client, token = await _make_scim_client(db_session, provider=provider)
    resolved = await resolve_scim_client(token, session=db_session)
    assert resolved is not None
    assert resolved.id == client.id


@pytest.mark.anyio
async def test_resolver_rejects_revoked_token(db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider, revoked=True)
    assert await resolve_scim_client(token, session=db_session) is None


@pytest.mark.anyio
async def test_resolver_rejects_disabled_provider(db_session):
    provider = await _make_provider(db_session)
    provider.enabled = False
    await db_session.commit()
    _, token = await _make_scim_client(db_session, provider=provider)
    assert await resolve_scim_client(token, session=db_session) is None


@pytest.mark.anyio
async def test_resolver_rejects_missing_scope(db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider, scopes=["other"])
    assert await resolve_scim_client(token, session=db_session) is None


# ---------------------------------------------------------------------------
# /Users CRUD
# ---------------------------------------------------------------------------


async def test_provision_new_user(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "alice@example.com",
        "name": {"givenName": "Alice", "familyName": "Doe"},
        "emails": [{"value": "alice@example.com", "primary": True}],
        "active": True,
        "externalId": "00ualice",
    }
    response = await client.post("/scim/v2/Users", json=body, headers=_bearer(token))
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["userName"] == "alice@example.com"
    assert payload["active"] is True
    user_id = uuid.UUID(payload["id"])

    # User exists in the DB.
    db_session.expire_all()
    user = await db_session.get(User, user_id)
    assert user is not None
    assert user.email == "alice@example.com"
    assert user.auth_source == "oidc"


async def test_provision_existing_email_is_idempotent(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    await _make_user(db_session, email="bob@example.com")

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "bob@example.com",
        "active": True,
    }
    response = await client.post("/scim/v2/Users", json=body, headers=_bearer(token))
    # Spec calls this "no_op" — handler returns 200 with the existing user.
    assert response.status_code == 200
    assert response.json()["userName"] == "bob@example.com"


async def test_put_replaces_user_and_disables(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    user = await _make_user(db_session, email="charlie@example.com")
    user_id = user.id

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "charlie@example.com",
        "name": {"givenName": "Charlie", "familyName": "B"},
        "active": False,
    }
    response = await client.put(
        f"/scim/v2/Users/{user_id}", json=body, headers=_bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    db_session.expire_all()
    refreshed = await db_session.get(User, user_id)
    assert refreshed is not None
    assert refreshed.is_active is False
    assert refreshed.deactivated_reason == "scim"


async def test_patch_active_false_cascades_pats(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    user = await _make_user(db_session, email="dora@example.com")
    user_id = user.id

    pat = PersonalAccessToken(
        user_id=user_id,
        name="legit",
        token_hash=secrets.token_bytes(32),
        token_prefix="cgr_pat_abcdef12",
        scopes=["api:read"],
    )
    db_session.add(pat)
    await db_session.commit()
    pat_id = pat.id

    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    response = await client.patch(
        f"/scim/v2/Users/{user_id}", json=body, headers=_bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    db_session.expire_all()
    refreshed_pat = await db_session.get(PersonalAccessToken, pat_id)
    assert refreshed_pat is not None
    assert refreshed_pat.revoked_at is not None
    assert refreshed_pat.revoked_reason == "idp_block"


async def test_delete_user_returns_204_and_disables(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    user = await _make_user(db_session, email="eve@example.com")
    user_id = user.id

    response = await client.delete(
        f"/scim/v2/Users/{user_id}", headers=_bearer(token)
    )
    assert response.status_code == 204

    db_session.expire_all()
    refreshed = await db_session.get(User, user_id)
    assert refreshed is not None
    assert refreshed.is_active is False


# ---------------------------------------------------------------------------
# Last-admin protection
# ---------------------------------------------------------------------------


async def test_last_admin_disable_via_scim_is_rejected_and_audited(client, db_session):
    """SCIM cannot disable the only remaining active admin/owner."""
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    # Single admin/owner — disabling them would leave the instance unreachable.
    only_admin = await _make_user(
        db_session, email="owner@example.com", role=UserRole.OWNER
    )
    only_admin_id = only_admin.id

    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    response = await client.patch(
        f"/scim/v2/Users/{only_admin_id}", json=body, headers=_bearer(token)
    )
    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "403"
    assert body["scimType"] == "mutability"

    db_session.expire_all()
    refreshed = await db_session.get(User, only_admin_id)
    assert refreshed is not None
    assert refreshed.is_active is True

    rows = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "scim_last_admin_disable_blocked"
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].severity == "critical"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_replayed_patch_is_deduped(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    user = await _make_user(db_session, email="frank@example.com")
    user_id = user.id

    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    headers = _bearer(token)

    first = await client.patch(f"/scim/v2/Users/{user_id}", json=body, headers=headers)
    assert first.status_code == 200
    for _ in range(4):
        replay = await client.patch(
            f"/scim/v2/Users/{user_id}", json=body, headers=headers
        )
        assert replay.status_code == 200

    db_session.expire_all()
    events = (
        await db_session.execute(
            select(SCIMEvent).where(SCIMEvent.target_user_id == user_id)
        )
    ).scalars().all()
    # Single applied row — all replays dedupe via uq_scim_events_idempotency.
    assert len(events) == 1
    assert events[0].status == "applied"


# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------


async def test_filter_username_eq_returns_match(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    await _make_user(db_session, email="grace@example.com")

    response = await client.get(
        '/scim/v2/Users?filter=userName eq "grace@example.com"',
        headers=_bearer(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalResults"] == 1
    assert body["Resources"][0]["userName"] == "grace@example.com"


async def test_unsupported_filter_returns_400(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    response = await client.get(
        '/scim/v2/Users?filter=name.givenName co "X"',
        headers=_bearer(token),
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidFilter"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


async def test_groups_returns_501(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    response = await client.get("/scim/v2/Groups", headers=_bearer(token))
    assert response.status_code == 501
    assert response.json()["scimType"] == "notImplemented"


async def test_no_csrf_header_required(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "harry@example.com",
        "active": True,
    }
    # No X-CSRF-Token, no cookie session — bearer is enough.
    response = await client.post("/scim/v2/Users", json=body, headers=_bearer(token))
    assert response.status_code == 201


async def test_service_provider_config(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)
    response = await client.get(
        "/scim/v2/ServiceProviderConfig", headers=_bearer(token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["patch"]["supported"] is True
    assert body["bulk"]["supported"] is False


async def test_idempotency_key_is_stable_under_key_reorder():
    payload_a = {"active": False, "userName": "x@example.com"}
    payload_b = {"userName": "x@example.com", "active": False}
    pid = uuid4()
    key_a = build_idempotency_key(
        provider_id=pid,
        external_id="eid",
        operation="patch",
        payload_hash=canonical_payload_hash(payload_a),
    )
    key_b = build_idempotency_key(
        provider_id=pid,
        external_id="eid",
        operation="patch",
        payload_hash=canonical_payload_hash(payload_b),
    )
    assert key_a == key_b


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------


async def _login_as(client, db_session, settings, *, role: UserRole) -> User:
    user = User(
        email=f"{role.value}@example.com",
        password_hash="hashed",
        name=role.value,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.headers["X-CSRF-Token"] = "csrf-token"
    return user


async def test_admin_list_scim_clients_works(client, db_session, settings):
    provider = await _make_provider(db_session)
    await _make_scim_client(db_session, provider=provider)
    await _login_as(client, db_session, settings, role=UserRole.ADMIN)

    response = await client.get("/api/admin/scim-clients")
    assert response.status_code == 200
    assert len(response.json()["clients"]) == 1


async def test_admin_can_create_scim_client(client, db_session, settings):
    """Admin and owner share one tier — admins can create SCIM clients."""
    provider = await _make_provider(db_session)
    provider_id = provider.id
    await _login_as(client, db_session, settings, role=UserRole.ADMIN)

    response = await client.post(
        "/api/admin/scim-clients",
        json={"provider_id": str(provider_id), "name": "x"},
    )
    assert response.status_code == 201


async def test_owner_creates_and_rotates_scim_client(client, db_session, settings):
    provider = await _make_provider(db_session)
    provider_id = provider.id
    await _login_as(client, db_session, settings, role=UserRole.OWNER)

    create = await client.post(
        "/api/admin/scim-clients",
        json={"provider_id": str(provider_id), "name": "Okta SCIM"},
    )
    assert create.status_code == 201
    plaintext = create.json()["token"]
    assert plaintext.startswith(PAT_PLAINTEXT_PREFIX)
    client_id = create.json()["view"]["id"]

    rotate = await client.post(f"/api/admin/scim-clients/{client_id}/rotate")
    assert rotate.status_code == 201
    new_plaintext = rotate.json()["token"]
    assert new_plaintext != plaintext

    db_session.expire_all()
    rows = (
        await db_session.execute(select(SCIMClient).where(SCIMClient.name == "Okta SCIM"))
    ).scalars().all()
    revoked = [r for r in rows if r.revoked_reason == "rotation"]
    assert len(revoked) == 1


async def test_owner_revokes_scim_client(client, db_session, settings):
    provider = await _make_provider(db_session)
    scim_client, _ = await _make_scim_client(db_session, provider=provider)
    scim_client_id = scim_client.id
    await _login_as(client, db_session, settings, role=UserRole.OWNER)

    response = await client.delete(f"/api/admin/scim-clients/{scim_client_id}")
    assert response.status_code == 204

    db_session.expire_all()
    refreshed = await db_session.get(SCIMClient, scim_client_id)
    assert refreshed is not None
    assert refreshed.revoked_reason == "admin"


# ---------------------------------------------------------------------------
# Provider deletion soft-revokes SCIM clients
# ---------------------------------------------------------------------------


async def test_provider_delete_softrevokes_scim_clients(client, db_session, settings):
    provider = await _make_provider(db_session)
    scim_client, _ = await _make_scim_client(db_session, provider=provider)
    provider_id = provider.id
    scim_client_id = scim_client.id
    await _login_as(client, db_session, settings, role=UserRole.OWNER)

    response = await client.delete(f"/api/admin/identity-providers/{provider_id}")
    assert response.status_code == 204

    db_session.expire_all()
    refreshed = await db_session.get(SCIMClient, scim_client_id)
    assert refreshed is not None
    assert refreshed.revoked_reason == "provider_deleted"
    # FK SET NULL applied on commit.
    assert refreshed.provider_id is None


# ---------------------------------------------------------------------------
# Layer-1 propagation — disabled user can't use a stale PAT
# ---------------------------------------------------------------------------


async def test_disabled_user_pat_rejected_at_rest(client, db_session):
    provider = await _make_provider(db_session)
    _, token = await _make_scim_client(db_session, provider=provider)

    user = await _make_user(db_session, email="iggy@example.com")
    user_id = user.id
    plaintext = f"{PAT_PLAINTEXT_PREFIX}{secrets.token_urlsafe(36)}"
    pat = PersonalAccessToken(
        user_id=user_id,
        name="legit",
        token_hash=hashlib.sha256(plaintext.encode()).digest(),
        token_prefix=plaintext[:PAT_PREFIX_DISPLAY_CHARS],
        scopes=["api:read", "api:write", "mcp"],
    )
    db_session.add(pat)
    await db_session.commit()

    # Sanity: PAT works while user is active. /api/me/tokens uses
    # `require_authenticated` which accepts PAT bearer.
    pre = await client.get(
        "/api/me/tokens", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert pre.status_code == 200

    # SCIM disables the user.
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }
    disable = await client.patch(
        f"/scim/v2/Users/{user_id}", json=body, headers=_bearer(token)
    )
    assert disable.status_code == 200

    # PAT now rejected: the row was revoked, AND the user is_active=False
    # (Layer-1 catches both).
    post = await client.get(
        "/api/me/tokens", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert post.status_code == 401
