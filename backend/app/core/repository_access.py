from __future__ import annotations

from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.enums import RepositoryVisibility, UserRole
from backend.app.models.group import GroupMember, RepositoryGrant
from backend.app.models.repository import Repository
from backend.app.models.user import User


def apply_repository_read_scope(
    statement: Select,
    *,
    settings: Settings,
    current_user: User | None,
) -> Select:
    """Funnel that scopes a `select(Repository)` to what `current_user` can see.

    Layering rules:

    * Soft-deleted rows are hidden from every caller (the background
      purge worker has not finished draining the cascade yet but the
      row is logically gone).
    * Anonymous callers: only PUBLIC repos, and only if
      `settings.auth.public_read` is on.
    * OWNER / ADMIN role: sees everything (short-circuit).
    * USER role: PUBLIC plus any repo granted to a group they belong
      to (READ / WRITE / ADMIN all imply READ). The grant subquery is
      a hash-semijoin against the `(group_id, user_id)` and
      `(repository_id)` indexes on `group_members` / `repository_grants`.

    This function is the single source of truth for both the REST list
    endpoints and the MCP `cograph_repositories` resource — they both
    funnel through here, so ACL propagates to MCP for free.
    """

    statement = statement.where(Repository.deleted_at.is_(None))

    if current_user is None:
        if not settings.auth.public_read:
            return statement.where(false())
        return statement.where(Repository.visibility == RepositoryVisibility.PUBLIC)

    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return statement

    granted_subq = (
        select(RepositoryGrant.repository_id)
        .join(GroupMember, GroupMember.group_id == RepositoryGrant.group_id)
        .where(GroupMember.user_id == current_user.id)
    )
    return statement.where(
        or_(
            Repository.visibility == RepositoryVisibility.PUBLIC,
            Repository.id.in_(granted_subq),
        )
    )


def can_read_repository_sync(
    repository: Repository,
    *,
    settings: Settings,
    current_user: User | None,
) -> bool:
    """Synchronous fast-path for the cases that don't need a DB hit.

    Returns True/False without touching grants:

    * deleted_at not None → False
    * anonymous + public_read + visibility=PUBLIC → True
    * OWNER/ADMIN role → True
    * USER with PUBLIC repo → True

    For USER on a non-PUBLIC repo, returns False — the caller must
    fall back to `has_repository_permission(...)` (which hits the
    grant tables) to know for sure. The async helpers in this module
    do that fallback automatically.
    """

    if repository.deleted_at is not None:
        return False
    if current_user is None:
        return (
            settings.auth.public_read
            and repository.visibility is RepositoryVisibility.PUBLIC
        )
    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return True
    return repository.visibility is RepositoryVisibility.PUBLIC


# Back-compat alias for callers that already imported the old name.
# New code should call the explicit `_sync` form or the async
# `_user_can_read_repository` helper below.
can_read_repository = can_read_repository_sync


async def _user_can_read_repository(
    session: AsyncSession,
    repository: Repository,
    *,
    settings: Settings,
    current_user: User | None,
) -> bool:
    """Full check: sync fast-path, then group-grant lookup if needed.

    Soft-deleted rows are invisible regardless of grants — the funnel
    SQL drops them at the top of `apply_repository_read_scope`, and the
    single-row path matches that invariant here. Otherwise the API
    list would hide a deleted repo but `GET /repos/{slug}` would
    return it for a user with a pre-existing grant.
    """

    if repository.deleted_at is not None:
        return False
    if can_read_repository_sync(
        repository, settings=settings, current_user=current_user
    ):
        return True
    if current_user is None:
        return False
    # USER tier, non-PUBLIC repo: check group grants.
    granted = await session.scalar(
        select(RepositoryGrant.repository_id)
        .join(GroupMember, GroupMember.group_id == RepositoryGrant.group_id)
        .where(
            GroupMember.user_id == current_user.id,
            RepositoryGrant.repository_id == repository.id,
        )
        .limit(1)
    )
    return granted is not None


async def get_readable_repository(
    *,
    session: AsyncSession,
    repository_id: UUID,
    settings: Settings,
    current_user: User | None,
) -> Repository:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    if not await _user_can_read_repository(
        session,
        repository,
        settings=settings,
        current_user=current_user,
    ):
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    return repository


async def get_readable_repository_by_slug(
    *,
    session: AsyncSession,
    host: str,
    owner: str,
    name: str,
    settings: Settings,
    current_user: User | None,
) -> Repository:
    """Resolve a Repository by its compound slug `host/owner/name`.

    Same 404 semantics as `get_readable_repository`: missing rows and
    rows the caller cannot read both surface as 404 to avoid leaking
    existence of private repos.
    """

    repository = await session.scalar(
        select(Repository).where(
            Repository.host == host,
            Repository.owner == owner,
            Repository.name == name,
        )
    )
    if repository is None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    if not await _user_can_read_repository(
        session,
        repository,
        settings=settings,
        current_user=current_user,
    ):
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    return repository
