from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.app.core.errors import ApiError
from backend.app.models.enums import MdCollectionVisibility, UserRole
from backend.app.models.group import CollectionGrant, GroupMember
from backend.app.models.md_collection import MdCollection
from backend.app.models.user import User


def apply_md_collection_read_scope(
    statement: Select,
    *,
    current_user: User | None,
) -> Select:
    """Funnel that scopes a `select(MdCollection)` by viewer.

    Layering rules:

    * Anonymous callers: only PUBLIC collections (collection-level
      `public_read` is not a knob today; we keep the historical
      behaviour).
    * OWNER / ADMIN role: sees everything (short-circuit).
    * USER role: PUBLIC plus any collection they own (`owner_id ==
      user.id` — pre-existing semantic) plus any collection granted
      to a group they belong to.

    Same MCP-propagation property as `apply_repository_read_scope`:
    `mcp/services.py:317` calls this function, so ACL flows into the
    `cograph_collections` resource without any MCP-side changes.
    """

    if current_user is None:
        return statement.where(
            MdCollection.visibility == MdCollectionVisibility.PUBLIC
        )

    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return statement

    granted_subq = (
        select(CollectionGrant.collection_id)
        .join(GroupMember, GroupMember.group_id == CollectionGrant.group_id)
        .where(GroupMember.user_id == current_user.id)
    )
    return statement.where(
        or_(
            MdCollection.visibility == MdCollectionVisibility.PUBLIC,
            MdCollection.owner_id == current_user.id,
            MdCollection.id.in_(granted_subq),
        )
    )


def can_read_md_collection_sync(
    collection: MdCollection,
    *,
    current_user: User | None,
) -> bool:
    """Synchronous fast-path. See `can_read_repository_sync` for the
    same shape. Returns False for USER on a non-PUBLIC, non-owned
    collection — caller must fall back to the async helper which
    consults the grant tables.
    """

    if collection.visibility is MdCollectionVisibility.PUBLIC:
        return True
    if current_user is None:
        return False
    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return True
    return collection.owner_id == current_user.id


# Back-compat alias.
can_read_md_collection = can_read_md_collection_sync


async def _user_can_read_md_collection(
    session: AsyncSession,
    collection: MdCollection,
    *,
    current_user: User | None,
) -> bool:
    """Full check: sync fast-path, then group-grant lookup."""

    if can_read_md_collection_sync(collection, current_user=current_user):
        return True
    if current_user is None:
        return False
    granted = await session.scalar(
        select(CollectionGrant.collection_id)
        .join(GroupMember, GroupMember.group_id == CollectionGrant.group_id)
        .where(
            GroupMember.user_id == current_user.id,
            CollectionGrant.collection_id == collection.id,
        )
        .limit(1)
    )
    return granted is not None


async def get_readable_md_collection(
    *,
    session: AsyncSession,
    collection_id: UUID,
    current_user: User | None,
) -> MdCollection:
    collection = await session.get(MdCollection, collection_id)
    if collection is None:
        raise ApiError(404, "NOT_FOUND", "Collection not found")
    if not await _user_can_read_md_collection(
        session, collection, current_user=current_user
    ):
        raise ApiError(403, "FORBIDDEN", "Collection access denied")
    return collection


async def list_readable_md_collections(
    *,
    session: AsyncSession,
    current_user: User | None,
) -> list[MdCollection]:
    rows = await session.scalars(
        apply_md_collection_read_scope(
            select(MdCollection),
            current_user=current_user,
        ).order_by(MdCollection.updated_at.desc(), MdCollection.id.desc())
    )
    return list(rows.all())
