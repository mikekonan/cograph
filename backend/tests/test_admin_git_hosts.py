"""Phase 30.5 — admin git host + credential CRUD + Test button."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest

from backend.app.core.auth import TokenType, create_token
from backend.app.git.credentials import GitCredentialCipher
from backend.app.models.enums import UserRole
from backend.app.models.git_credential import GitCredential
from backend.app.models.user import User


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


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_owner_creates_and_lists_hosts(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    create = await client.post(
        "/api/admin/git-hosts",
        json={
            "slug": "github-com",
            "display_name": "GitHub.com",
            "kind": "github",
            "base_url": "https://github.com/",
            "api_url": "https://api.github.com/",
            "git_host": "github.com",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["slug"] == "github-com"
    assert body["base_url"] == "https://github.com"  # trailing slash stripped
    assert body["api_url"] == "https://api.github.com"

    listing = await client.get("/api/admin/git-hosts")
    assert listing.status_code == 200
    assert len(listing.json()["hosts"]) == 1


@pytest.mark.anyio
async def test_admin_can_list_and_create(client, db_session, settings):
    """Admin and owner share one tier — admins can create git hosts."""
    await _login_as(client, db_session, settings, role=UserRole.ADMIN)
    create = await client.post(
        "/api/admin/git-hosts",
        json={
            "slug": "github-com",
            "display_name": "GitHub.com",
            "kind": "github",
            "base_url": "https://github.com",
            "api_url": "https://api.github.com",
            "git_host": "github.com",
        },
    )
    assert create.status_code == 201

    listing = await client.get("/api/admin/git-hosts")
    assert listing.status_code == 200


@pytest.mark.anyio
async def test_duplicate_host_returns_409(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    payload = {
        "slug": "github-com",
        "display_name": "GitHub.com",
        "kind": "github",
        "base_url": "https://github.com",
        "api_url": "https://api.github.com",
        "git_host": "github.com",
    }
    first = await client.post("/api/admin/git-hosts", json=payload)
    assert first.status_code == 201

    second = await client.post(
        "/api/admin/git-hosts",
        json={**payload, "slug": "github-com-other"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "GIT_HOST_CONFLICT"


@pytest.mark.anyio
async def test_delete_host_in_use_returns_409(client, db_session, settings):
    from backend.app.models.repository import Repository

    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    create = await client.post(
        "/api/admin/git-hosts",
        json={
            "slug": "ghes-example",
            "display_name": "GHES",
            "kind": "github",
            "base_url": "https://git.example.com",
            "api_url": "https://git.example.com/api/v3",
            "git_host": "git.example.com",
        },
    )
    host_id = create.json()["id"]
    host_uuid = uuid.UUID(host_id)

    db_session.add(
        Repository(
            git_url="https://git.example.com/o/r",
            host="git.example.com",
            host_id=host_uuid,
            owner="o",
            name="r",
            branch="main",
        )
    )
    await db_session.commit()

    delete = await client.delete(f"/api/admin/git-hosts/{host_id}")
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "HOST_IN_USE"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


async def _create_host(client, *, slug: str = "github-com", git_host: str = "github.com") -> str:
    create = await client.post(
        "/api/admin/git-hosts",
        json={
            "slug": slug,
            "display_name": slug,
            "kind": "github",
            "base_url": f"https://{git_host}",
            "api_url": f"https://api.{git_host}",
            "git_host": git_host,
        },
    )
    assert create.status_code == 201
    return create.json()["id"]


@pytest.mark.anyio
async def test_owner_creates_credential_and_token_never_returned(
    client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)

    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": "ghp_supersecrettoken123", "is_default": True},
    )
    assert create.status_code == 201
    body = create.json()
    assert body["token_prefix"] == "ghp_supersec"  # 12 chars
    assert "token" not in body  # plaintext never echoed
    assert "token_encrypted" not in body
    assert body["is_default"] is True


@pytest.mark.anyio
async def test_setting_default_clears_other_defaults(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)

    a = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "a", "token": "ghp_aaa", "is_default": True},
    )
    a_id = a.json()["id"]
    b = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "b", "token": "ghp_bbb", "is_default": True},
    )
    assert b.status_code == 201

    listing = await client.get(f"/api/admin/git-hosts/{host_id}/credentials")
    rows = listing.json()["credentials"]
    a_row = next(r for r in rows if r["id"] == a_id)
    assert a_row["is_default"] is False


@pytest.mark.anyio
async def test_credential_token_round_trips_through_cipher(
    client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)
    plaintext = "ghp_persisted_value_123"

    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": plaintext, "is_default": True},
    )
    cred_id = uuid.UUID(create.json()["id"])

    row = await db_session.get(GitCredential, cred_id)
    cipher = GitCredentialCipher(settings)
    assert cipher.decrypt(row.token_encrypted) == plaintext


# ---------------------------------------------------------------------------
# Test button
# ---------------------------------------------------------------------------


def _mock_httpx_response(status_code: int, *, json_body=None, headers=None):
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://api.github.com/user"),
        json=json_body or {},
        headers=headers or {},
    )


@pytest.mark.anyio
async def test_test_credential_ok(client, db_session, settings):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)
    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": "ghp_token", "is_default": True},
    )
    cred_id_str = create.json()["id"]
    cred_id = uuid.UUID(cred_id_str)

    async def _fake_get(self, url, **kwargs):
        assert url.endswith("/user")
        assert kwargs["headers"]["Authorization"] == "Bearer ghp_token"
        return _mock_httpx_response(
            200,
            json_body={"login": "octocat"},
            headers={"x-oauth-scopes": "repo, read:org"},
        )

    with patch.object(httpx.AsyncClient, "get", _fake_get):
        resp = await client.post(
            f"/api/admin/git-hosts/{host_id}/credentials/{cred_id_str}/test",
            json={},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["login"] == "octocat"
    assert body["scopes"] == ["repo", "read:org"]

    row = await db_session.get(GitCredential, cred_id)
    await db_session.refresh(row)
    assert row.last_test_status == "ok"
    assert row.scopes_observed == ["repo", "read:org"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status_code, expected_status",
    [(401, "unauthorized"), (403, "forbidden")],
)
async def test_test_credential_unauthorized_or_forbidden(
    client, db_session, settings, status_code, expected_status
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)
    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": "ghp_token", "is_default": True},
    )
    cred_id = create.json()["id"]

    async def _fake_get(self, url, **kwargs):
        return _mock_httpx_response(status_code)

    with patch.object(httpx.AsyncClient, "get", _fake_get):
        resp = await client.post(
            f"/api/admin/git-hosts/{host_id}/credentials/{cred_id}/test",
            json={},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == expected_status


@pytest.mark.anyio
async def test_test_credential_network_redacts_token(
    client, db_session, settings
):
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)
    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": "ghp_super_sentinel", "is_default": True},
    )
    cred_id = create.json()["id"]

    async def _fake_get(self, url, **kwargs):
        raise httpx.ConnectError(
            "could not connect using token=ghp_super_sentinel"
        )

    with patch.object(httpx.AsyncClient, "get", _fake_get):
        resp = await client.post(
            f"/api/admin/git-hosts/{host_id}/credentials/{cred_id}/test",
            json={},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "network"
    assert "ghp_super_sentinel" not in (body["error"] or "")
    assert "***" in (body["error"] or "")


@pytest.mark.anyio
async def test_test_credential_with_override_token(client, db_session, settings):
    """Test-before-save: owner pastes a token; we hit /user with it but
    don't overwrite the persisted ciphertext."""
    await _login_as(client, db_session, settings, role=UserRole.OWNER)
    host_id = await _create_host(client)
    create = await client.post(
        f"/api/admin/git-hosts/{host_id}/credentials",
        json={"label": "ops", "token": "ghp_persisted", "is_default": True},
    )
    cred_id_str = create.json()["id"]
    cred_id = uuid.UUID(cred_id_str)

    captured: dict[str, str] = {}

    async def _fake_get(self, url, **kwargs):
        captured["auth"] = kwargs["headers"]["Authorization"]
        return _mock_httpx_response(200, json_body={"login": "x"}, headers={})

    with patch.object(httpx.AsyncClient, "get", _fake_get):
        resp = await client.post(
            f"/api/admin/git-hosts/{host_id}/credentials/{cred_id_str}/test",
            json={"token": "ghp_override"},
        )
    assert resp.status_code == 200
    assert captured["auth"] == "Bearer ghp_override"

    row = await db_session.get(GitCredential, cred_id)
    cipher = GitCredentialCipher(settings)
    assert cipher.decrypt(row.token_encrypted) == "ghp_persisted"
