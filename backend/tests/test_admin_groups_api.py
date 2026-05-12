"""Tests for /api/admin/groups CRUD + membership + grants endpoints.

Covers:
* Auth gate (USER → 403, ADMIN/OWNER → 200).
* Groups CRUD (create / list / patch / delete) including 409 on
  duplicate name and 404 on missing id.
* Member management — bulk add idempotency, 404 on unknown user id,
  remove-not-found.
* Repository / collection grants — upsert (200 on first put, 200 on
  level bump), 404 when the target resource doesn't exist or is
  soft-deleted, delete returns 204.
* Audit row written for every mutation (one event_type per mutation
  kind).

The audit-event assertions are the load-bearing part: if a future
refactor drops the `write_audit` calls the read-side admin endpoints
would keep working, but compliance/forensics would lose the trail.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.audit_event import AuditEvent
from backend.app.models.enums import (
    RepoSource,
    RepositoryStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.group import (
    CollectionGrant,
    Group,
    GroupMember,
    RepositoryGrant,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.repository import Repository
from backend.app.models.user import User


_TEST_CSRF = "csrf-token"


async def _auth(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


def _csrf() -> dict[str, str]:
    return {"X-CSRF-Token": _TEST_CSRF}


async def _make_user(
    db_session, *, role: UserRole = UserRole.USER, email: str | None = None
) -> User:
    user = User(
        id=uuid4(),
        email=email or f"u-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("password-1234"),
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url=f"https://example.com/acme/r-{uuid4().hex[:6]}.git",
        name=f"r-{uuid4().hex[:6]}",
        owner="acme",
        branch="main",
        source=RepoSource.GIT,
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(repo)
    await db_session.commit()
    return repo


async def _make_collection(db_session) -> MdCollection:
    coll = MdCollection(
        name=f"c-{uuid4().hex[:8]}",
        description="",
        visibility="private",
    )
    db_session.add(coll)
    await db_session.commit()
    return coll


async def _audit_types(db_session) -> list[str]:
    rows = (
        await db_session.scalars(
            select(AuditEvent.event_type).order_by(AuditEvent.created_at.asc())
        )
    ).all()
    return list(rows)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_list_groups_forbidden_for_user(client, db_session, settings):
    user = await _make_user(db_session)
    await _auth(client, settings, user)
    response = await client.get("/api/admin/groups")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_list_groups_ok_for_admin(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    await _auth(client, settings, admin)
    response = await client.get("/api/admin/groups")
    assert response.status_code == 200
    assert response.json() == {"items": []}


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


async def test_create_group_succeeds_and_writes_audit(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.OWNER)
    await _auth(client, settings, admin)

    response = await client.post(
        "/api/admin/groups",
        json={"name": "engineers", "description": "all engineers"},
        headers=_csrf(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "engineers"
    assert body["description"] == "all engineers"
    assert body["member_count"] == 0
    assert body["repository_grant_count"] == 0
    assert body["collection_grant_count"] == 0
    assert UUID(body["id"])
    assert body["created_by"] == str(admin.id)

    assert "group_created" in await _audit_types(db_session)


async def test_create_group_rejects_duplicate_name(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    await _auth(client, settings, admin)

    response = await client.post(
        "/api/admin/groups", json={"name": "ops"}, headers=_csrf()
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/admin/groups", json={"name": "ops"}, headers=_csrf()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NAME_TAKEN"


async def test_list_groups_returns_counts(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    member = await _make_user(db_session)
    repo = await _make_repo(db_session)
    group = Group(name="leads")
    db_session.add(group)
    await db_session.commit()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            RepositoryGrant(
                group_id=group.id, repository_id=repo.id, level="read"
            ),
        ]
    )
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.get("/api/admin/groups")
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["name"] == "leads"
    assert row["member_count"] == 1
    assert row["repository_grant_count"] == 1
    assert row["collection_grant_count"] == 0


async def test_patch_group_renames_and_writes_audit(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.OWNER)
    group = Group(name="old-name", description="old")
    db_session.add(group)
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.patch(
        f"/api/admin/groups/{group.id}",
        json={"name": "new-name", "description": "new"},
        headers=_csrf(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "new-name"
    assert body["description"] == "new"
    assert "group_renamed" in await _audit_types(db_session)


async def test_patch_group_404_when_missing(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    await _auth(client, settings, admin)
    response = await client.patch(
        f"/api/admin/groups/{uuid4()}",
        json={"name": "nope"},
        headers=_csrf(),
    )
    assert response.status_code == 404


async def test_delete_group_cascades_members_and_grants(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.OWNER)
    member = await _make_user(db_session)
    repo = await _make_repo(db_session)
    coll = await _make_collection(db_session)
    group = Group(name="gone")
    db_session.add(group)
    await db_session.commit()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            RepositoryGrant(group_id=group.id, repository_id=repo.id, level="read"),
            CollectionGrant(
                group_id=group.id, collection_id=coll.id, level="write"
            ),
        ]
    )
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.delete(
        f"/api/admin/groups/{group.id}", headers=_csrf()
    )
    assert response.status_code == 204

    # FK ON DELETE CASCADE means all child rows are gone.
    member_count = await db_session.scalar(
        select(func.count()).select_from(GroupMember).where(
            GroupMember.group_id == group.id
        )
    )
    repo_grants = await db_session.scalar(
        select(func.count()).select_from(RepositoryGrant).where(
            RepositoryGrant.group_id == group.id
        )
    )
    coll_grants = await db_session.scalar(
        select(func.count()).select_from(CollectionGrant).where(
            CollectionGrant.group_id == group.id
        )
    )
    assert (member_count, repo_grants, coll_grants) == (0, 0, 0)
    assert "group_deleted" in await _audit_types(db_session)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def test_add_members_idempotent_bulk(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    u1 = await _make_user(db_session)
    u2 = await _make_user(db_session)
    group = Group(name="batch")
    db_session.add(group)
    await db_session.commit()

    await _auth(client, settings, admin)

    # First add: both new.
    response = await client.post(
        f"/api/admin/groups/{group.id}/members",
        json={"user_ids": [str(u1.id), str(u2.id)]},
        headers=_csrf(),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["added"]) == {str(u1.id), str(u2.id)}
    assert body["already_present"] == []

    # Re-send: both already present.
    response = await client.post(
        f"/api/admin/groups/{group.id}/members",
        json={"user_ids": [str(u1.id), str(u2.id)]},
        headers=_csrf(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == []
    assert set(body["already_present"]) == {str(u1.id), str(u2.id)}


async def test_add_members_404_when_user_missing(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="g")
    db_session.add(group)
    await db_session.commit()
    await _auth(client, settings, admin)

    response = await client.post(
        f"/api/admin/groups/{group.id}/members",
        json={"user_ids": [str(uuid4())]},
        headers=_csrf(),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "USER_NOT_FOUND"


async def test_remove_member_204_and_audit(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    member = await _make_user(db_session)
    group = Group(name="g")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=member.id))
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.delete(
        f"/api/admin/groups/{group.id}/members/{member.id}",
        headers=_csrf(),
    )
    assert response.status_code == 204
    assert "group_member_removed" in await _audit_types(db_session)


# ---------------------------------------------------------------------------
# Repository grants
# ---------------------------------------------------------------------------


async def test_put_repo_grant_creates_and_upgrades(
    client, db_session, settings
):
    """First PUT creates the grant; second PUT at a different level
    upgrades it in place. Both return 200, audited as added then
    updated — proves we don't accidentally re-emit a "_added" event
    on the upgrade path.
    """
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="grantees")
    repo = await _make_repo(db_session)
    db_session.add(group)
    await db_session.commit()

    await _auth(client, settings, admin)

    # Create.
    response = await client.post(
        f"/api/admin/groups/{group.id}/repositories",
        json={"repository_id": str(repo.id), "level": "read"},
        headers=_csrf(),
    )
    assert response.status_code == 200
    assert response.json()["level"] == "read"

    # Upgrade to ADMIN.
    response = await client.post(
        f"/api/admin/groups/{group.id}/repositories",
        json={"repository_id": str(repo.id), "level": "admin"},
        headers=_csrf(),
    )
    assert response.status_code == 200
    assert response.json()["level"] == "admin"

    audit_types = await _audit_types(db_session)
    assert "repo_grant_added" in audit_types
    assert "repo_grant_updated" in audit_types


async def test_put_repo_grant_404_when_repo_missing(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="g")
    db_session.add(group)
    await db_session.commit()
    await _auth(client, settings, admin)

    response = await client.post(
        f"/api/admin/groups/{group.id}/repositories",
        json={"repository_id": str(uuid4()), "level": "read"},
        headers=_csrf(),
    )
    assert response.status_code == 404


async def test_put_repo_grant_404_on_soft_deleted_repo(
    client, db_session, settings
):
    """Soft-deleted repos must not accept new grants — the funnel
    hides them from reads anyway, but a stale UI could still try to
    create one. The API rejects.
    """
    from datetime import UTC, datetime

    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repo = await _make_repo(db_session)
    repo.deleted_at = datetime.now(UTC)
    group = Group(name="g")
    db_session.add(group)
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.post(
        f"/api/admin/groups/{group.id}/repositories",
        json={"repository_id": str(repo.id), "level": "read"},
        headers=_csrf(),
    )
    assert response.status_code == 404


async def test_delete_repo_grant_204_and_audit(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="g")
    repo = await _make_repo(db_session)
    db_session.add(group)
    await db_session.commit()
    db_session.add(
        RepositoryGrant(group_id=group.id, repository_id=repo.id, level="write")
    )
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.delete(
        f"/api/admin/groups/{group.id}/repositories/{repo.id}",
        headers=_csrf(),
    )
    assert response.status_code == 204
    assert "repo_grant_removed" in await _audit_types(db_session)


async def test_list_repo_grants_returns_slug_and_level(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="g")
    repo = await _make_repo(db_session)
    db_session.add(group)
    await db_session.commit()
    db_session.add(
        RepositoryGrant(group_id=group.id, repository_id=repo.id, level="admin")
    )
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.get(f"/api/admin/groups/{group.id}/repositories")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["repository_slug"] == f"{repo.host}/{repo.owner}/{repo.name}"
    assert items[0]["level"] == "admin"


# ---------------------------------------------------------------------------
# Collection grants
# ---------------------------------------------------------------------------


async def test_put_collection_grant_creates_and_upgrades(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="grantees")
    coll = await _make_collection(db_session)
    db_session.add(group)
    await db_session.commit()
    await _auth(client, settings, admin)

    response = await client.post(
        f"/api/admin/groups/{group.id}/collections",
        json={"collection_id": str(coll.id), "level": "read"},
        headers=_csrf(),
    )
    assert response.status_code == 200
    assert response.json()["level"] == "read"

    response = await client.post(
        f"/api/admin/groups/{group.id}/collections",
        json={"collection_id": str(coll.id), "level": "write"},
        headers=_csrf(),
    )
    assert response.status_code == 200
    assert response.json()["level"] == "write"

    audit_types = await _audit_types(db_session)
    assert "collection_grant_added" in audit_types
    assert "collection_grant_updated" in audit_types


async def test_delete_collection_grant_204_and_audit(
    client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    group = Group(name="g")
    coll = await _make_collection(db_session)
    db_session.add(group)
    await db_session.commit()
    db_session.add(
        CollectionGrant(group_id=group.id, collection_id=coll.id, level="read")
    )
    await db_session.commit()

    await _auth(client, settings, admin)
    response = await client.delete(
        f"/api/admin/groups/{group.id}/collections/{coll.id}",
        headers=_csrf(),
    )
    assert response.status_code == 204
    assert "collection_grant_removed" in await _audit_types(db_session)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


async def test_create_group_requires_csrf(client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    await _auth(client, settings, admin)

    # No X-CSRF-Token header => 403 CSRF_INVALID at the dep layer.
    response = await client.post("/api/admin/groups", json={"name": "csrf-test"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_grant_level_change_uses_helper_to_swap_repo_visibility(
    client, db_session, settings
):
    """End-to-end smoke: a USER who joins a group with READ on an
    ADMIN_ONLY repo immediately sees it in `GET /api/repos`, and
    the visibility funnel matches `apply_repository_read_scope`.

    This isn't strictly an admin_groups test — it ties admin_groups
    to the funnel rewrite from C1 so a regression in either layer
    surfaces here.
    """
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    member = await _make_user(db_session)
    repo = await _make_repo(db_session)

    # 1. Admin creates a group via the API.
    await _auth(client, settings, admin)
    response = await client.post(
        "/api/admin/groups", json={"name": "viewers"}, headers=_csrf()
    )
    assert response.status_code == 201
    group_id = response.json()["id"]

    # 2. Adds the member.
    response = await client.post(
        f"/api/admin/groups/{group_id}/members",
        json={"user_ids": [str(member.id)]},
        headers=_csrf(),
    )
    assert response.status_code == 200

    # 3. Grants READ on the repo.
    response = await client.post(
        f"/api/admin/groups/{group_id}/repositories",
        json={"repository_id": str(repo.id), "level": "read"},
        headers=_csrf(),
    )
    assert response.status_code == 200

    # 4. Switch sessions to the member and confirm the repo shows up.
    client.cookies.clear()
    await _auth(client, settings, member)
    response = await client.get("/api/repos")
    assert response.status_code == 200
    body = response.json()
    slugs = {f"{item['host']}/{item['owner']}/{item['name']}" for item in body["items"]}
    assert f"{repo.host}/{repo.owner}/{repo.name}" in slugs


