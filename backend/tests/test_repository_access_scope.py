"""Read-scope tests for `apply_repository_read_scope`.

Covers the matrix: anonymous / USER / ADMIN viewers crossed with
PUBLIC / ADMIN_ONLY (granted / not granted) / soft-deleted rows.
The funnel is the single point that REST list endpoints AND the MCP
`cograph.repositories` resource go through, so getting the matrix
right here proves the contract for both surfaces simultaneously.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.core.repository_access import (
    apply_repository_read_scope,
    get_readable_repository_by_slug,
)
from backend.app.core.errors import ApiError
from backend.app.models.enums import (
    GrantLevel,
    RepoSource,
    RepositoryStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.group import Group, GroupMember, RepositoryGrant
from backend.app.models.repository import Repository
from backend.app.models.user import User


def _make_repo(
    *,
    name: str,
    visibility: RepositoryVisibility,
    deleted: bool = False,
) -> Repository:
    return Repository(
        id=uuid4(),
        host="example.com",
        owner="acme",
        name=name,
        git_url=f"https://example.com/acme/{name}.git",
        branch="main",
        source=RepoSource.GIT,
        status=RepositoryStatus.READY,
        visibility=visibility,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


async def _make_user(db_session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _seed_matrix(db_session) -> dict[str, Repository]:
    public_repo = _make_repo(name="pub", visibility=RepositoryVisibility.PUBLIC)
    private_repo = _make_repo(name="priv", visibility=RepositoryVisibility.ADMIN_ONLY)
    granted_repo = _make_repo(name="granted", visibility=RepositoryVisibility.ADMIN_ONLY)
    deleted_repo = _make_repo(
        name="gone", visibility=RepositoryVisibility.PUBLIC, deleted=True
    )
    deleted_granted = _make_repo(
        name="gone-granted",
        visibility=RepositoryVisibility.ADMIN_ONLY,
        deleted=True,
    )
    db_session.add_all([public_repo, private_repo, granted_repo, deleted_repo, deleted_granted])
    await db_session.commit()
    return {
        "public": public_repo,
        "private": private_repo,
        "granted": granted_repo,
        "deleted_public": deleted_repo,
        "deleted_granted": deleted_granted,
    }


async def _grant_to_group(
    db_session, *, user: User, repo: Repository, level: GrantLevel
) -> None:
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


async def _list_visible(db_session, settings, user) -> set[str]:
    rows = await db_session.scalars(
        apply_repository_read_scope(
            select(Repository), settings=settings, current_user=user
        )
    )
    return {repo.name for repo in rows.all()}


async def test_anonymous_sees_only_public_when_public_read_on(
    db_session, settings: Settings
) -> None:
    await _seed_matrix(db_session)
    visible = await _list_visible(db_session, settings, None)
    assert visible == {"pub"}  # private hidden, granted hidden, deleted hidden


async def test_anonymous_sees_nothing_when_public_read_off(
    db_session, settings: Settings
) -> None:
    await _seed_matrix(db_session)
    # Mutate the settings instance for this test only.
    settings_local = settings.model_copy(deep=True)
    settings_local.auth.public_read = False
    visible = await _list_visible(db_session, settings_local, None)
    assert visible == set()


async def test_admin_role_sees_every_live_row(db_session, settings) -> None:
    repos = await _seed_matrix(db_session)
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    visible = await _list_visible(db_session, settings, admin)
    # All non-deleted rows visible regardless of visibility.
    assert visible == {"pub", "priv", "granted"}
    # Soft-deleted ALWAYS hidden, even from admin.
    assert "gone" not in visible
    assert "gone-granted" not in visible
    del repos  # silence unused-var lint


async def test_owner_role_sees_every_live_row(db_session, settings) -> None:
    await _seed_matrix(db_session)
    owner = await _make_user(db_session, role=UserRole.OWNER)
    visible = await _list_visible(db_session, settings, owner)
    assert visible == {"pub", "priv", "granted"}


async def test_user_without_grants_sees_only_public(
    db_session, settings
) -> None:
    await _seed_matrix(db_session)
    user = await _make_user(db_session)
    visible = await _list_visible(db_session, settings, user)
    assert visible == {"pub"}


async def test_user_with_group_grant_sees_granted_repo(
    db_session, settings
) -> None:
    repos = await _seed_matrix(db_session)
    user = await _make_user(db_session)
    await _grant_to_group(
        db_session, user=user, repo=repos["granted"], level=GrantLevel.READ
    )
    visible = await _list_visible(db_session, settings, user)
    assert visible == {"pub", "granted"}
    # The other ADMIN_ONLY repo (no grant) stays hidden.
    assert "priv" not in visible


async def test_grant_on_soft_deleted_repo_is_ignored(
    db_session, settings
) -> None:
    """Soft-delete must override grants — a USER in a group that
    happens to hold a grant on a now-deleted repo must NOT see it.
    """
    repos = await _seed_matrix(db_session)
    user = await _make_user(db_session)
    await _grant_to_group(
        db_session,
        user=user,
        repo=repos["deleted_granted"],
        level=GrantLevel.ADMIN,
    )
    visible = await _list_visible(db_session, settings, user)
    assert visible == {"pub"}


async def test_slug_getter_404s_for_user_without_grant(
    db_session, settings
) -> None:
    repos = await _seed_matrix(db_session)
    user = await _make_user(db_session)
    priv = repos["private"]
    with pytest.raises(ApiError) as exc:
        await get_readable_repository_by_slug(
            session=db_session,
            host=priv.host,
            owner=priv.owner,
            name=priv.name,
            settings=settings,
            current_user=user,
        )
    assert exc.value.status_code == 404


async def test_slug_getter_returns_repo_for_user_with_grant(
    db_session, settings
) -> None:
    repos = await _seed_matrix(db_session)
    user = await _make_user(db_session)
    await _grant_to_group(
        db_session, user=user, repo=repos["granted"], level=GrantLevel.READ
    )
    granted = repos["granted"]
    found = await get_readable_repository_by_slug(
        session=db_session,
        host=granted.host,
        owner=granted.owner,
        name=granted.name,
        settings=settings,
        current_user=user,
    )
    assert found.id == granted.id


async def test_slug_getter_404s_for_user_on_soft_deleted(
    db_session, settings
) -> None:
    """Even with a grant, a soft-deleted repo is invisible — same
    rule as the funnel test above, on the single-row path."""
    repos = await _seed_matrix(db_session)
    user = await _make_user(db_session)
    await _grant_to_group(
        db_session,
        user=user,
        repo=repos["deleted_granted"],
        level=GrantLevel.ADMIN,
    )
    gone = repos["deleted_granted"]
    with pytest.raises(ApiError) as exc:
        await get_readable_repository_by_slug(
            session=db_session,
            host=gone.host,
            owner=gone.owner,
            name=gone.name,
            settings=settings,
            current_user=user,
        )
    assert exc.value.status_code == 404
