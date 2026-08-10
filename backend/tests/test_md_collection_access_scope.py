"""Read-scope tests for `apply_md_collection_read_scope`.

Parallel to test_repository_access_scope.py — covers anonymous /
USER / ADMIN viewers crossed with PUBLIC / PRIVATE / ADMIN_ONLY
collections, with and without group grants. Additionally verifies the
pre-existing `owner_id == current_user.id` self-access semantic is
preserved through the funnel rewrite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.core.errors import ApiError
from backend.app.core.md_collection_access import (
    apply_md_collection_read_scope,
    get_readable_md_collection,
)
from backend.app.models.enums import (
    GrantLevel,
    MdCollectionVisibility,
    UserRole,
)
from backend.app.models.group import CollectionGrant, Group, GroupMember
from backend.app.models.md_collection import MdCollection
from backend.app.models.user import User


async def _make_user(db_session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _coll(
    *,
    name: str,
    visibility: MdCollectionVisibility,
    owner_id=None,
) -> MdCollection:
    return MdCollection(
        id=uuid4(),
        name=name,
        owner_id=owner_id,
        visibility=visibility,
    )


async def _grant(
    db_session, *, user: User, collection: MdCollection, level: GrantLevel
) -> None:
    group = Group(id=uuid4(), name=f"g-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    db_session.add(
        CollectionGrant(
            group_id=group.id, collection_id=collection.id, level=level.value
        )
    )
    await db_session.commit()


async def _list_visible(db_session, user) -> set[str]:
    rows = await db_session.scalars(
        apply_md_collection_read_scope(
            select(MdCollection), current_user=user
        )
    )
    return {coll.name for coll in rows.all()}


async def test_anonymous_sees_only_public(db_session) -> None:
    pub = _coll(name=f"pub-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PUBLIC)
    priv = _coll(name=f"priv-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PRIVATE)
    db_session.add_all([pub, priv])
    await db_session.commit()
    visible = await _list_visible(db_session, None)
    assert pub.name in visible
    assert priv.name not in visible


async def test_admin_sees_every_collection(db_session) -> None:
    pub = _coll(name=f"pub-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PUBLIC)
    priv = _coll(name=f"priv-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PRIVATE)
    admin_only = _coll(
        name=f"adm-{uuid4().hex[:6]}",
        visibility=MdCollectionVisibility.ADMIN_ONLY,
    )
    db_session.add_all([pub, priv, admin_only])
    await db_session.commit()
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    visible = await _list_visible(db_session, admin)
    assert {pub.name, priv.name, admin_only.name}.issubset(visible)


async def test_user_sees_only_public_without_grant_or_ownership(
    db_session,
) -> None:
    pub = _coll(name=f"pub-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PUBLIC)
    priv = _coll(name=f"priv-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PRIVATE)
    db_session.add_all([pub, priv])
    await db_session.commit()
    user = await _make_user(db_session)
    visible = await _list_visible(db_session, user)
    assert visible == {pub.name}


async def test_user_sees_own_private_collection(db_session) -> None:
    """The pre-existing `owner_id == current_user.id` semantic must
    survive the funnel rewrite — otherwise we'd silently take away
    access from users who created their own private collections.
    """
    user = await _make_user(db_session)
    pub = _coll(name=f"pub-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PUBLIC)
    mine = _coll(
        name=f"mine-{uuid4().hex[:6]}",
        visibility=MdCollectionVisibility.PRIVATE,
        owner_id=user.id,
    )
    other_priv = _coll(
        name=f"other-{uuid4().hex[:6]}",
        visibility=MdCollectionVisibility.PRIVATE,
    )
    db_session.add_all([pub, mine, other_priv])
    await db_session.commit()
    visible = await _list_visible(db_session, user)
    assert visible == {pub.name, mine.name}
    assert other_priv.name not in visible


async def test_user_sees_granted_collection(db_session) -> None:
    user = await _make_user(db_session)
    granted = _coll(
        name=f"granted-{uuid4().hex[:6]}",
        visibility=MdCollectionVisibility.PRIVATE,
    )
    ungranted = _coll(
        name=f"ungranted-{uuid4().hex[:6]}",
        visibility=MdCollectionVisibility.ADMIN_ONLY,
    )
    db_session.add_all([granted, ungranted])
    await db_session.commit()
    await _grant(db_session, user=user, collection=granted, level=GrantLevel.READ)
    visible = await _list_visible(db_session, user)
    assert granted.name in visible
    assert ungranted.name not in visible


async def test_get_readable_md_collection_403s_for_user_without_access(
    db_session,
) -> None:
    user = await _make_user(db_session)
    priv = _coll(
        name=f"priv-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PRIVATE
    )
    db_session.add(priv)
    await db_session.commit()
    with pytest.raises(ApiError) as exc:
        await get_readable_md_collection(
            session=db_session, collection_id=priv.id, current_user=user
        )
    assert exc.value.status_code == 403


async def test_get_readable_md_collection_succeeds_for_grant_holder(
    db_session,
) -> None:
    user = await _make_user(db_session)
    priv = _coll(
        name=f"priv-{uuid4().hex[:6]}", visibility=MdCollectionVisibility.PRIVATE
    )
    db_session.add(priv)
    await db_session.commit()
    await _grant(db_session, user=user, collection=priv, level=GrantLevel.READ)
    found = await get_readable_md_collection(
        session=db_session, collection_id=priv.id, current_user=user
    )
    assert found.id == priv.id
