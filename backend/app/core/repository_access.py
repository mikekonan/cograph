from __future__ import annotations

from sqlalchemy import false, select
from sqlalchemy.sql import Select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.enums import RepositoryVisibility
from backend.app.models.repository import Repository
from backend.app.models.user import User


def apply_repository_read_scope(
    statement: Select,
    *,
    settings: Settings,
    current_user: User | None,
) -> Select:
    # Soft-deleted repositories must vanish from every read path from
    # the instant the DELETE click lands — the background purge worker
    # has not yet drained the cascade, but the user must not see the
    # row, and the sync scheduler must not pick it up.
    statement = statement.where(Repository.deleted_at.is_(None))
    if current_user is not None:
        return statement
    if not settings.auth.public_read:
        return statement.where(false())
    return statement.where(Repository.visibility == RepositoryVisibility.PUBLIC)


def can_read_repository(
    repository: Repository,
    *,
    settings: Settings,
    current_user: User | None,
) -> bool:
    if repository.deleted_at is not None:
        return False
    if current_user is not None:
        return True
    return (
        settings.auth.public_read
        and repository.visibility is RepositoryVisibility.PUBLIC
    )


async def get_readable_repository(
    *,
    session: AsyncSession,
    repository_id,
    settings: Settings,
    current_user: User | None,
) -> Repository:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    if not can_read_repository(
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

    Same 404 semantics as `get_readable_repository`: missing rows and rows
    the caller cannot read both surface as 404 to avoid leaking existence
    of private repos.
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
    if not can_read_repository(
        repository,
        settings=settings,
        current_user=current_user,
    ):
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    return repository
