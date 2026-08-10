"""Admin API tests for the MCP operator briefing.

Covers the three things that matter for a singleton-row admin
endpoint:

- non-admin users are 403'd on both GET and PATCH,
- PATCH updates `content`, bumps `updated_at`, stamps
  `updated_by_user_id`, and returns the resolved email,
- oversized content is rejected at the schema layer.

The migration seeds `id=1` with empty content, so GET right after
boot is a 200 — never a 404.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.enums import UserRole
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing
from backend.app.models.user import User

_TEST_CSRF = "csrf-token"


async def _make_user(
    db_session,
    *,
    email: str,
    role: UserRole = UserRole.USER,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


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


async def test_get_briefing_requires_admin(client, db_session, settings):
    plain = await _make_user(db_session, email="plain@example.com")
    await _authenticate(client, settings, plain)
    response = await client.get("/api/admin/mcp/briefing")
    assert response.status_code == 403


async def test_patch_briefing_requires_admin(client, db_session, settings):
    plain = await _make_user(db_session, email="plain@example.com")
    await _authenticate(client, settings, plain)
    response = await client.patch(
        "/api/admin/mcp/briefing",
        json={"content": "hi"},
        headers={"x-csrf-token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_get_briefing_returns_seeded_singleton(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/mcp/briefing")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == ""
    assert body["updated_by_user_id"] is None
    assert body["updated_by_email"] is None


async def test_patch_briefing_updates_content_and_attribution(
    client, db_session, settings
):
    admin = await _make_user(db_session, email="admin@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, admin)

    # GET first to materialise the singleton, then snapshot updated_at.
    await client.get("/api/admin/mcp/briefing")
    await db_session.commit()
    before_row = (
        await db_session.execute(
            select(McpOperatorBriefing).where(McpOperatorBriefing.id == 1)
        )
    ).scalar_one()
    before = before_row.updated_at

    response = await client.patch(
        "/api/admin/mcp/briefing",
        json={"content": "## Team payments\nGlossary: acquirer = …"},
        headers={"x-csrf-token": _TEST_CSRF},
    )
    assert response.status_code == 200
    body = response.json()
    assert "## Team payments" in body["content"]
    assert body["updated_by_user_id"] == str(admin.id)
    assert body["updated_by_email"] == "admin@example.com"

    # updated_at advanced
    parsed = datetime.fromisoformat(body["updated_at"])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    assert parsed >= before


async def test_patch_briefing_rejects_oversized_payload(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, admin)
    response = await client.patch(
        "/api/admin/mcp/briefing",
        json={"content": "x" * 8001},
        headers={"x-csrf-token": _TEST_CSRF},
    )
    assert response.status_code == 422


async def test_get_briefing_after_patch_reflects_change(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, admin)
    await client.patch(
        "/api/admin/mcp/briefing",
        json={"content": "round-trip"},
        headers={"x-csrf-token": _TEST_CSRF},
    )
    response = await client.get("/api/admin/mcp/briefing")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "round-trip"
    assert body["updated_by_email"] == "root@example.com"
