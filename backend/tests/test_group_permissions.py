"""Tests for `has_repository_permission` / `has_collection_permission`.

Covers the ladder semantics (READ < WRITE — higher grant satisfies
lower required level), the OWNER/ADMIN role short-circuit, the
collection-owner self-access shortcut, and the case where a user is in
two groups whose grants on the same resource have different levels
(the helper must pick the highest).

GrantLevel.ADMIN was retired in migration 0052 — destructive endpoints
now gate at the dependency layer via `require_admin_or_owner` instead.
"""

from __future__ import annotations

from uuid import uuid4

from backend.app.core.group_permissions import (
    has_collection_permission,
    has_repository_permission,
)
from backend.app.models.enums import (
    GrantLevel,
    MdCollectionVisibility,
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


async def _make_user(db_session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        id=uuid4(),
        host="example.com",
        owner="acme",
        name=f"r-{uuid4().hex[:8]}",
        git_url=f"https://example.com/acme/r-{uuid4().hex[:8]}.git",
        branch="main",
        source=RepoSource.GIT,
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(repo)
    await db_session.commit()
    return repo


async def _make_collection(db_session, *, owner_id=None) -> MdCollection:
    coll = MdCollection(
        id=uuid4(),
        name=f"c-{uuid4().hex[:8]}",
        owner_id=owner_id,
        visibility=MdCollectionVisibility.PRIVATE,
    )
    db_session.add(coll)
    await db_session.commit()
    return coll


async def _grant_repo(
    db_session, *, user: User, repo: Repository, level: GrantLevel
) -> Group:
    group = Group(id=uuid4(), name=f"g-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    db_session.add(
        RepositoryGrant(
            group_id=group.id, repository_id=repo.id, level=level.value
        )
    )
    await db_session.commit()
    return group


async def _grant_coll(
    db_session, *, user: User, coll: MdCollection, level: GrantLevel
) -> None:
    group = Group(id=uuid4(), name=f"g-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    db_session.add(
        CollectionGrant(
            group_id=group.id, collection_id=coll.id, level=level.value
        )
    )
    await db_session.commit()


async def test_repo_admin_role_always_passes(db_session) -> None:
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repo = await _make_repo(db_session)
    for level in (GrantLevel.READ, GrantLevel.WRITE):
        assert await has_repository_permission(db_session, admin, repo.id, level)


async def test_repo_owner_role_always_passes(db_session) -> None:
    owner = await _make_user(db_session, role=UserRole.OWNER)
    repo = await _make_repo(db_session)
    assert await has_repository_permission(
        db_session, owner, repo.id, GrantLevel.WRITE
    )


async def test_repo_anonymous_always_fails(db_session) -> None:
    repo = await _make_repo(db_session)
    assert not await has_repository_permission(
        db_session, None, repo.id, GrantLevel.READ
    )


async def test_repo_user_without_grant_fails(db_session) -> None:
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    assert not await has_repository_permission(
        db_session, user, repo.id, GrantLevel.READ
    )


async def test_repo_read_grant_does_not_satisfy_write(db_session) -> None:
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.READ)
    assert await has_repository_permission(
        db_session, user, repo.id, GrantLevel.READ
    )
    assert not await has_repository_permission(
        db_session, user, repo.id, GrantLevel.WRITE
    )


async def test_repo_write_grant_satisfies_read_and_write(db_session) -> None:
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)
    assert await has_repository_permission(
        db_session, user, repo.id, GrantLevel.READ
    )
    assert await has_repository_permission(
        db_session, user, repo.id, GrantLevel.WRITE
    )


async def test_repo_highest_grant_across_multiple_groups_wins(
    db_session,
) -> None:
    """A user in two groups, one with READ and one with WRITE on the
    same repo, must be treated as WRITE. The helper picks max via
    `func.max(case(...))`, but if that breaks we'd silently regress
    users who got upgraded via a second group membership.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.READ)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)
    assert await has_repository_permission(
        db_session, user, repo.id, GrantLevel.WRITE
    )


async def test_collection_owner_passes_without_grant(db_session) -> None:
    """Pre-existing semantic: a user owning their private collection
    must keep WRITE access without any group grant. If we silently
    broke this in the helper rewrite, every user-created private
    collection would become read-only-for-self.
    """
    user = await _make_user(db_session)
    coll = await _make_collection(db_session, owner_id=user.id)
    for level in (GrantLevel.READ, GrantLevel.WRITE):
        assert await has_collection_permission(db_session, user, coll, level)
        # also via UUID overload — same answer.
        assert await has_collection_permission(db_session, user, coll.id, level)


async def test_collection_non_owner_user_without_grant_fails(
    db_session,
) -> None:
    user = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner_id=other.id)
    assert not await has_collection_permission(
        db_session, user, coll, GrantLevel.READ
    )


async def test_collection_write_grant_satisfies_read_and_write(
    db_session,
) -> None:
    user = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner_id=other.id)
    await _grant_coll(db_session, user=user, coll=coll, level=GrantLevel.WRITE)
    assert await has_collection_permission(
        db_session, user, coll, GrantLevel.READ
    )
    assert await has_collection_permission(
        db_session, user, coll, GrantLevel.WRITE
    )


async def test_collection_read_grant_does_not_satisfy_write(db_session) -> None:
    user = await _make_user(db_session)
    other = await _make_user(db_session)
    coll = await _make_collection(db_session, owner_id=other.id)
    await _grant_coll(db_session, user=user, coll=coll, level=GrantLevel.READ)
    assert await has_collection_permission(
        db_session, user, coll, GrantLevel.READ
    )
    assert not await has_collection_permission(
        db_session, user, coll, GrantLevel.WRITE
    )
