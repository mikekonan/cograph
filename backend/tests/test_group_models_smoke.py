"""Smoke tests for the Group / ACL ORM models.

These don't exercise behaviour — they just pin down the wiring so a
later refactor that breaks the SQLAlchemy mapping fails fast with a
clear test, rather than as a confusing import error in some unrelated
endpoint test. The behavioural coverage of the funnel + helpers lives
in `test_repository_access_scope.py`, `test_md_collection_access_scope.py`,
and `test_group_permissions.py`.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from backend.app.db.base import Base
from backend.app.models.enums import (
    GrantLevel,
    MdCollectionVisibility,
    UserRole,
)
from backend.app.models.group import (
    CollectionGrant,
    Group,
    GroupMember,
    RepositoryGrant,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.user import User


def test_grant_level_enum_values() -> None:
    # The values matter — they are the literal strings stored in the
    # `level` CHECK column on the grant tables and in the
    # `repository_grants_level_check` / `collection_grants_level_check`
    # constraints in migration 0050.
    assert GrantLevel.READ.value == "read"
    assert GrantLevel.WRITE.value == "write"
    assert GrantLevel.ADMIN.value == "admin"


def test_group_acl_tables_registered_on_base_metadata() -> None:
    tables = set(Base.metadata.tables.keys())
    assert {
        "groups",
        "group_members",
        "repository_grants",
        "collection_grants",
    }.issubset(tables)


def test_repository_grants_table_columns() -> None:
    cols = {c.name for c in Base.metadata.tables["repository_grants"].columns}
    assert cols == {
        "group_id",
        "repository_id",
        "level",
        "granted_at",
        "granted_by",
    }


def test_collection_grants_table_columns() -> None:
    cols = {c.name for c in Base.metadata.tables["collection_grants"].columns}
    assert cols == {
        "group_id",
        "collection_id",
        "level",
        "granted_at",
        "granted_by",
    }


async def _make_user(db_session) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_collection(db_session, owner_id) -> MdCollection:
    coll = MdCollection(
        id=uuid4(),
        name=f"coll-{uuid4().hex[:8]}",
        owner_id=owner_id,
        visibility=MdCollectionVisibility.PRIVATE,
    )
    db_session.add(coll)
    await db_session.commit()
    return coll


async def test_group_create_and_member_roundtrip(db_session) -> None:
    """End-to-end: create a Group, add a real user, read it back.
    This catches FK / PK problems that a static metadata check would
    miss (e.g. wrong on-delete cascade ordering, mistyped PK column).
    """
    user = await _make_user(db_session)

    group = Group(
        id=uuid4(),
        name=f"acme-{uuid4().hex[:8]}",
        description="smoke",
    )
    db_session.add(group)
    await db_session.commit()

    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    await db_session.commit()

    members = await db_session.scalar(
        select(func.count(GroupMember.user_id)).where(
            GroupMember.group_id == group.id
        )
    )
    assert members == 1


async def test_repository_grant_accepts_each_documented_level(
    db_session,
) -> None:
    """Schema accepts the three documented level strings. The CHECK
    constraint at the DB level (Postgres in prod; advisory on SQLite)
    is exercised in the migration; here we verify the ORM mapping.
    """
    from backend.app.models.enums import RepoSource, RepositoryStatus
    from backend.app.models.repository import Repository

    group = Group(id=uuid4(), name=f"grants-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()

    repos = [
        Repository(
            id=uuid4(),
            host="example.com",
            owner="acme",
            name=f"r-{i}-{uuid4().hex[:6]}",
            git_url=f"https://example.com/acme/r-{i}.git",
            branch="main",
            source=RepoSource.GIT,
            status=RepositoryStatus.READY,
        )
        for i in range(3)
    ]
    db_session.add_all(repos)
    await db_session.commit()

    db_session.add_all(
        [
            RepositoryGrant(
                group_id=group.id,
                repository_id=repos[0].id,
                level=GrantLevel.READ.value,
            ),
            RepositoryGrant(
                group_id=group.id,
                repository_id=repos[1].id,
                level=GrantLevel.WRITE.value,
            ),
            RepositoryGrant(
                group_id=group.id,
                repository_id=repos[2].id,
                level=GrantLevel.ADMIN.value,
            ),
        ]
    )
    await db_session.commit()

    rows = await db_session.scalars(
        select(RepositoryGrant.level).where(RepositoryGrant.group_id == group.id)
    )
    assert sorted(rows.all()) == ["admin", "read", "write"]


async def test_collection_grant_smoke_insert(db_session) -> None:
    user = await _make_user(db_session)
    coll = await _make_collection(db_session, owner_id=user.id)

    group = Group(id=uuid4(), name=f"colls-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()

    db_session.add(
        CollectionGrant(
            group_id=group.id,
            collection_id=coll.id,
            level=GrantLevel.READ.value,
        )
    )
    await db_session.commit()
