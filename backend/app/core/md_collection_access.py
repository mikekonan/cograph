from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.app.core.errors import ApiError
from backend.app.models.enums import MdCollectionVisibility
from backend.app.models.md_collection import MdCollection
from backend.app.models.user import User


def apply_md_collection_read_scope(
    statement: Select,
    *,
    current_user: User | None,
) -> Select:
    if current_user is not None:
        return statement
    return statement.where(MdCollection.visibility == MdCollectionVisibility.PUBLIC)


def can_read_md_collection(
    collection: MdCollection,
    *,
    current_user: User | None,
) -> bool:
    return (
        current_user is not None
        or collection.visibility == MdCollectionVisibility.PUBLIC
    )


async def get_readable_md_collection(
    *,
    session: AsyncSession,
    collection_id,
    current_user: User | None,
) -> MdCollection:
    collection = await session.get(MdCollection, collection_id)
    if collection is None:
        raise ApiError(404, "NOT_FOUND", "Collection not found")
    if not can_read_md_collection(collection, current_user=current_user):
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
