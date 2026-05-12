"""ACL-mutation tests for `/api/md-collections` endpoints.

Parallel to `test_repos_mutation_authz.py`. Proves:

* USER who is not the collection's owner and has no grant: 403 on
  mutation routes (the read funnel hides private collections, so
  `GET` is also 403/404 — but the mutation gate uses an explicit
  `_require_collection_for_mutation` ladder check, not the funnel).
* READ-grant USER trying WRITE → 403.
* WRITE-grant USER trying DELETE → 403 (DELETE is ADMIN here).
* WRITE-grant USER trying upload/PATCH → 200/201.
* ADMIN-grant USER → DELETE 204.
* Pre-existing `owner_id == current_user.id` shortcut still works
  (covered by existing tests in `test_md_collections_api.py`).
"""

from __future__ import annotations

from uuid import uuid4

from backend.app.core.auth import TokenType, create_token
from backend.app.models.enums import GrantLevel, UserRole
from backend.app.models.group import CollectionGrant, Group, GroupMember
from backend.app.models.md_collection import MdCollection
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


async def _make_user(db_session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_collection(
    db_session, *, owner: User | None = None, visibility: str = "private"
) -> MdCollection:
    coll = MdCollection(
        name=f"c-{uuid4().hex[:8]}",
        description="",
        visibility=visibility,
        owner_id=owner.id if owner else None,
    )
    db_session.add(coll)
    await db_session.commit()
    return coll


async def _grant(
    db_session, *, user: User, collection: MdCollection, level: GrantLevel
) -> None:
    group = Group(name=f"g-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    db_session.add(
        CollectionGrant(
            group_id=group.id,
            collection_id=collection.id,
            level=level.value,
        )
    )
    await db_session.commit()


# ----- PATCH (WRITE) -------------------------------------------------------


async def test_patch_collection_denied_for_user_without_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _auth(client, settings, other)

    response = await client.patch(
        f"/api/md-collections/{coll.id}",
        json={"description": "hijacked"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_patch_collection_denied_for_read_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.READ)
    await _auth(client, settings, other)

    response = await client.patch(
        f"/api/md-collections/{coll.id}",
        json={"description": "no write rung"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_patch_collection_allowed_for_write_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.WRITE)
    await _auth(client, settings, other)

    response = await client.patch(
        f"/api/md-collections/{coll.id}",
        json={"description": "edited by grantee"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "edited by grantee"


# ----- DELETE (ADMIN) ------------------------------------------------------


async def test_delete_collection_denied_for_write_grant(
    client, db_session, settings
):
    """WRITE must NOT satisfy DELETE — the ladder check distinguishes
    the two ranks. Regression guard for ladder-comparison bugs.
    """
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(
        db_session, user=other, collection=coll, level=GrantLevel.WRITE
    )
    await _auth(client, settings, other)

    response = await client.delete(
        f"/api/md-collections/{coll.id}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_delete_collection_allowed_for_admin_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(
        db_session, user=other, collection=coll, level=GrantLevel.ADMIN
    )
    await _auth(client, settings, other)

    response = await client.delete(
        f"/api/md-collections/{coll.id}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 204


# ----- upload (WRITE) ------------------------------------------------------


async def test_batch_upload_denied_for_read_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.READ)
    await _auth(client, settings, other)

    response = await client.post(
        f"/api/md-collections/{coll.id}/documents/batch",
        json={"documents": [{"source_key": "hi.md", "content": "# Hi"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_batch_upload_allowed_for_write_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.WRITE)
    await _auth(client, settings, other)

    response = await client.post(
        f"/api/md-collections/{coll.id}/documents/batch",
        json={"documents": [{"source_key": "hi.md", "content": "# Hi"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["indexed_documents"] == 1


# ----- re-embed (WRITE) ----------------------------------------------------


async def test_reembed_denied_for_read_grant(
    client, db_session, settings
):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.READ)
    await _auth(client, settings, other)

    response = await client.post(
        f"/api/md-collections/{coll.id}/re-embed",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_reembed_allowed_for_write_grant(client, db_session, settings):
    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _grant(db_session, user=other, collection=coll, level=GrantLevel.WRITE)
    await _auth(client, settings, other)

    response = await client.post(
        f"/api/md-collections/{coll.id}/re-embed",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "embed"


# ----- owner shortcut preserved -------------------------------------------


async def test_owner_can_still_delete_without_admin_grant(
    client, db_session, settings
):
    """Pre-existing semantic: the collection's `owner_id` user can
    perform ADMIN operations (delete) on their own collection without
    any group grant. If this breaks, every user who created their own
    private collection would lose the ability to delete it.
    """
    owner = await _make_user(db_session)
    coll = await _make_collection(db_session, owner=owner)
    await _auth(client, settings, owner)

    response = await client.delete(
        f"/api/md-collections/{coll.id}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 204
