"""Tests for /api/admin/users CRUD endpoints (Phase 30.1)."""

from __future__ import annotations

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.enums import UserRole
from backend.app.models.user import User


_TEST_CSRF = "csrf-token"


async def _authenticate(client, settings, user: User) -> None:
    """Wire access + csrf cookies for `user` so subsequent requests pass auth."""
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


async def _make_user(
    db_session,
    *,
    email: str,
    role: UserRole = UserRole.USER,
    name: str | None = None,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        name=name,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_list_users_requires_admin(client, db_session, settings):
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, member)

    response = await client.get("/api/admin/users")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_list_users_returns_all_rows_for_admin(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, owner)

    response = await client.get("/api/admin/users")

    assert response.status_code == 200
    items = response.json()["items"]
    assert {row["email"] for row in items} == {"owner@example.com", "member@example.com"}
    owner_row = next(row for row in items if row["email"] == "owner@example.com")
    assert owner_row["role"] == "owner"
    assert owner_row["is_active"] is True


async def test_create_user_succeeds(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "new@example.com",
            "password": "another-pass-1",
            "name": "New User",
            "role": "user",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "user"
    assert body["auth_source"] == "password"

    created = await db_session.scalar(select(User).where(User.email == "new@example.com"))
    assert created is not None
    assert created.password_hash != "another-pass-1"


async def test_create_admin_requires_owner(client, db_session, settings):
    """Only the owner can create new admins; admins can only create users."""
    plain_admin = await _make_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, plain_admin)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "new-admin@example.com",
            "password": "another-pass-1",
            "role": "admin",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_OWNER_ONLY"


async def test_create_owner_role_forbidden(client, db_session, settings):
    """role=owner via POST is rejected; transfer-owner is the only path."""
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "another-owner@example.com",
            "password": "another-pass-1",
            "role": "owner",
        },
    )

    assert response.status_code == 403


async def test_create_user_rejects_duplicate_email(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _make_user(db_session, email="taken@example.com")
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "taken@example.com",
            "password": "another-pass-1",
            "role": "user",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_create_user_rejects_short_password(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "new@example.com",
            "password": "short",  # 5 chars, < 10
            "role": "user",
        },
    )

    # Pydantic Field min_length triggers a 422 from FastAPI's validator path.
    assert response.status_code == 422


async def test_create_user_ignores_extra_fields(client, db_session, settings):
    """extra=forbid in CreateUserRequest rejects unknown fields like is_owner."""
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        "/api/admin/users",
        headers=_csrf_headers(),
        json={
            "email": "new@example.com",
            "password": "another-pass-1",
            "role": "admin",
            "is_owner": True,
        },
    )

    assert response.status_code == 422


async def test_patch_owner_can_change_role(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, owner)

    response = await client.patch(
        f"/api/admin/users/{member.id}",
        headers=_csrf_headers(),
        json={"role": "admin", "name": "Promoted"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["name"] == "Promoted"


async def test_patch_role_change_requires_owner(client, db_session, settings):
    """Plain admin cannot change another user's role — owner-only."""
    plain_admin = await _make_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ADMIN,
    )
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, plain_admin)

    response = await client.patch(
        f"/api/admin/users/{member.id}",
        headers=_csrf_headers(),
        json={"role": "admin"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_OWNER_ONLY"


async def test_patch_user_resets_password(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    original_hash = member.password_hash
    await _authenticate(client, settings, owner)

    response = await client.patch(
        f"/api/admin/users/{member.id}",
        headers=_csrf_headers(),
        json={"password": "new-secure-password"},
    )

    assert response.status_code == 200
    await db_session.refresh(member)
    assert member.password_hash != original_hash


async def test_patch_owner_role_change_forbidden(client, db_session, settings):
    """Owner cannot demote themselves via PATCH; transfer-owner is the only path."""
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.patch(
        f"/api/admin/users/{owner.id}",
        headers=_csrf_headers(),
        json={"role": "user"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_PROTECTED"


async def test_patch_promote_to_owner_forbidden(client, db_session, settings):
    """Owner cannot promote someone to owner via PATCH — transfer-owner is the only path."""
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, owner)

    response = await client.patch(
        f"/api/admin/users/{member.id}",
        headers=_csrf_headers(),
        json={"role": "owner"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_PROTECTED"


async def test_disable_user_succeeds(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, owner)

    response = await client.post(
        f"/api/admin/users/{member.id}/disable",
        headers=_csrf_headers(),
        json={"reason": "off-boarded"},
    )

    assert response.status_code == 204
    await db_session.refresh(member)
    assert member.is_active is False
    assert member.deactivated_reason == "off-boarded"


async def test_disable_owner_blocked(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    plain_admin = await _make_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, plain_admin)

    response = await client.post(
        f"/api/admin/users/{owner.id}/disable",
        headers=_csrf_headers(),
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_PROTECTED"
    await db_session.refresh(owner)
    assert owner.is_active is True


async def test_enable_user(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    member.is_active = False
    await db_session.commit()
    await _authenticate(client, settings, owner)

    response = await client.post(
        f"/api/admin/users/{member.id}/enable",
        headers=_csrf_headers(),
    )

    assert response.status_code == 204
    await db_session.refresh(member)
    assert member.is_active is True


async def test_delete_user_succeeds(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    member = await _make_user(db_session, email="member@example.com")
    await _authenticate(client, settings, owner)

    response = await client.delete(
        f"/api/admin/users/{member.id}",
        headers=_csrf_headers(),
    )

    assert response.status_code == 204
    deleted = await db_session.scalar(select(User).where(User.id == member.id))
    assert deleted is None


async def test_delete_owner_forbidden(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    plain_admin = await _make_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, plain_admin)

    response = await client.delete(
        f"/api/admin/users/{owner.id}",
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_PROTECTED"


async def test_delete_self_forbidden(client, db_session, settings):
    admin_a = await _make_user(
        db_session,
        email="admin-a@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, admin_a)

    response = await client.delete(
        f"/api/admin/users/{admin_a.id}",
        headers=_csrf_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SELF_DELETE"


async def test_delete_user_requires_admin(client, db_session, settings):
    member = await _make_user(db_session, email="member@example.com")
    target = await _make_user(db_session, email="target@example.com")
    await _authenticate(client, settings, member)

    response = await client.delete(
        f"/api/admin/users/{target.id}",
        headers=_csrf_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_delete_user_not_found(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    await _authenticate(client, settings, owner)

    response = await client.delete(
        "/api/admin/users/00000000-0000-0000-0000-000000000000",
        headers=_csrf_headers(),
    )

    assert response.status_code == 404


async def test_transfer_owner_swaps_roles(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    target = await _make_user(
        db_session,
        email="target@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        f"/api/admin/users/{target.id}/transfer-owner",
        headers=_csrf_headers(),
        json={"confirm_email": "target@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["new_owner_id"] == str(target.id)
    assert body["previous_owner_id"] == str(owner.id)

    await db_session.refresh(owner)
    await db_session.refresh(target)
    assert owner.role is UserRole.ADMIN
    assert target.role is UserRole.OWNER

    # Audit row landed.
    from backend.app.models.audit_event import AuditEvent
    audit_rows = (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "transfer_owner")
        )
    ).all()
    assert len(audit_rows) == 1


async def test_transfer_owner_requires_owner(client, db_session, settings):
    plain_admin = await _make_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ADMIN,
    )
    target = await _make_user(
        db_session,
        email="target@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, plain_admin)

    response = await client.post(
        f"/api/admin/users/{target.id}/transfer-owner",
        headers=_csrf_headers(),
        json={"confirm_email": "target@example.com"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_OWNER_ONLY"


async def test_transfer_owner_email_mismatch(client, db_session, settings):
    owner = await _make_user(
        db_session,
        email="owner@example.com",
        role=UserRole.OWNER,
    )
    target = await _make_user(
        db_session,
        email="target@example.com",
        role=UserRole.ADMIN,
    )
    await _authenticate(client, settings, owner)

    response = await client.post(
        f"/api/admin/users/{target.id}/transfer-owner",
        headers=_csrf_headers(),
        json={"confirm_email": "wrong@example.com"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_TRANSFER_TARGET_INVALID"
