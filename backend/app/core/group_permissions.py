"""Mutation-side permission checks for group ACL grants.

The read-scope funnels in `repository_access` / `md_collection_access`
already decide what shows up in *list* endpoints. These helpers are
the per-row checks called by *mutation* handlers (reindex, upload,
delete, etc.) to decide whether `current_user` may act on the row
they already passed through the slug-resolver 404 guard.

Two design notes worth keeping in mind:

1. **N+1 caveat.** The helpers do one SQL roundtrip per call. They're
   intended for single-resource mutation handlers, not for filtering a
   list — the list path must use the read-scope funnel which does the
   semijoin in-database. Calling these inside a loop is a bug.

2. **PAT scope independence.** Personal access tokens carry their own
   scope set (`api:read`, `api:write`, `mcp`) enforced at the
   dependency-injection layer (`backend.app.core.deps`). A PAT with
   only `api:read` whose owning user is in a WRITE group still gets
   rejected at `deps.py` with `INSUFFICIENT_SCOPE` before this helper
   ever runs. ACL is layered on top of scope, not under it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.enums import GrantLevel, UserRole
from backend.app.models.group import (
    CollectionGrant,
    GroupMember,
    RepositoryGrant,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.user import User

_LEVEL_TO_RANK: dict[str, int] = {
    GrantLevel.READ.value: 1,
    GrantLevel.WRITE.value: 2,
}


def _required_rank(required: GrantLevel) -> int:
    return _LEVEL_TO_RANK[required.value]


async def has_repository_permission(
    session: AsyncSession,
    user: User | None,
    repository_id: UUID,
    required: GrantLevel,
) -> bool:
    """Return True iff `user` may act on the repo at `required` level.

    OWNER / ADMIN role passes immediately. Anonymous always fails
    (mutation endpoints already require auth at the dependency layer;
    this guard is belt-and-braces). USER role is satisfied if any of
    their groups holds a grant on the repository whose `level` rank
    is >= the required rank.
    """

    if user is None:
        return False
    if user.role in (UserRole.OWNER, UserRole.ADMIN):
        return True

    rank_case = case(
        (RepositoryGrant.level == GrantLevel.WRITE.value, 2),
        else_=1,
    )
    best = await session.scalar(
        select(func.max(rank_case))
        .select_from(RepositoryGrant)
        .join(GroupMember, GroupMember.group_id == RepositoryGrant.group_id)
        .where(
            GroupMember.user_id == user.id,
            RepositoryGrant.repository_id == repository_id,
        )
    )
    if best is None:
        return False
    return int(best) >= _required_rank(required)


async def has_collection_permission(
    session: AsyncSession,
    user: User | None,
    collection: MdCollection | UUID,
    required: GrantLevel,
) -> bool:
    """Return True iff `user` may act on the collection at `required` level.

    Same shape as `has_repository_permission`, plus the existing
    semantic that the collection's `owner_id` is always allowed (this
    predates the ACL layer; we keep it so existing users don't lose
    access to their own collections).

    Accepts either an already-loaded `MdCollection` (so the caller can
    avoid a second roundtrip when they have the row) or a bare
    `UUID`; in the latter case `owner_id` is checked via the grant
    SELECT itself.
    """

    if user is None:
        return False
    if user.role in (UserRole.OWNER, UserRole.ADMIN):
        return True

    if isinstance(collection, MdCollection):
        if collection.owner_id == user.id:
            return True
        collection_id = collection.id
    else:
        collection_id = collection
        # Owner-of-collection shortcut without an extra roundtrip.
        owner_id = await session.scalar(
            select(MdCollection.owner_id).where(MdCollection.id == collection_id)
        )
        if owner_id == user.id:
            return True

    rank_case = case(
        (CollectionGrant.level == GrantLevel.WRITE.value, 2),
        else_=1,
    )
    best = await session.scalar(
        select(func.max(rank_case))
        .select_from(CollectionGrant)
        .join(GroupMember, GroupMember.group_id == CollectionGrant.group_id)
        .where(
            GroupMember.user_id == user.id,
            CollectionGrant.collection_id == collection_id,
        )
    )
    if best is None:
        return False
    return int(best) >= _required_rank(required)
