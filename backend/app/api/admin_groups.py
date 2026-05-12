"""Admin group + ACL management endpoints.

CRUD over `groups`, `group_members`, `repository_grants` and
`collection_grants` for OWNER/ADMIN-tier callers. Mounted at
`/api/admin/groups` alongside the other `admin_*.py` routers.

Layering:

* Auth: every route requires OWNER/ADMIN via `require_admin_or_owner`.
  USER-role callers get 403 at the dependency layer; there is no
  "group manager" sub-role in v1.
* CSRF: every mutation goes through `require_csrf`, matching the
  pattern in `admin_users.py`.
* Audit: every mutation calls `write_audit` with a domain-specific
  event_type (group_*, *_grant_*, *_member_*), so the audit table
  records who-changed-what for compliance / forensics.

The router exposes 13 endpoints across 4 sections — groups CRUD,
membership, repository grants, collection grants. Bulk member-add is
idempotent so the UI can resend the full membership list without
worrying about duplicates.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
)
from backend.app.core.errors import ApiError
from backend.app.models.enums import GrantLevel
from backend.app.models.group import (
    CollectionGrant,
    Group,
    GroupMember,
    RepositoryGrant,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.repository import Repository
from backend.app.models.user import User


router = APIRouter(prefix="/admin/groups", tags=["admin", "groups"])


# ---------------------------------------------------------------------------
# Response/request schemas
# ---------------------------------------------------------------------------


class GroupResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    created_by: UUID | None
    member_count: int
    repository_grant_count: int
    collection_grant_count: int


class GroupListResponse(BaseModel):
    items: list[GroupResponse]


class CreateGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)


class UpdateGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)


class GroupMemberResponse(BaseModel):
    user_id: UUID
    email: str
    name: str | None
    added_at: datetime
    added_by: UUID | None


class GroupMembersResponse(BaseModel):
    items: list[GroupMemberResponse]


class AddMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[UUID] = Field(min_length=1, max_length=256)


class AddMembersResponse(BaseModel):
    added: list[UUID]
    already_present: list[UUID]


class RepositoryGrantResponse(BaseModel):
    repository_id: UUID
    repository_slug: str
    level: GrantLevel
    granted_at: datetime
    granted_by: UUID | None


class RepositoryGrantListResponse(BaseModel):
    items: list[RepositoryGrantResponse]


class PutRepositoryGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    level: GrantLevel


class CollectionGrantResponse(BaseModel):
    collection_id: UUID
    collection_name: str
    level: GrantLevel
    granted_at: datetime
    granted_by: UUID | None


class CollectionGrantListResponse(BaseModel):
    items: list[CollectionGrantResponse]


class PutCollectionGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_id: UUID
    level: GrantLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_group_or_404(session: AsyncSession, group_id: UUID) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        raise ApiError(404, "NOT_FOUND", "Group not found")
    return group


async def _group_counts(
    session: AsyncSession, group_id: UUID
) -> tuple[int, int, int]:
    """Return (member_count, repo_grant_count, collection_grant_count).

    Three scalar counts — cheap on the indexed `group_id` columns. The
    list endpoint runs this per-row, which is fine for the small N of
    groups expected in a single tenant (we're not paginating in v1).
    """
    members = await session.scalar(
        select(func.count())
        .select_from(GroupMember)
        .where(GroupMember.group_id == group_id)
    )
    repos = await session.scalar(
        select(func.count())
        .select_from(RepositoryGrant)
        .where(RepositoryGrant.group_id == group_id)
    )
    colls = await session.scalar(
        select(func.count())
        .select_from(CollectionGrant)
        .where(CollectionGrant.group_id == group_id)
    )
    return (int(members or 0), int(repos or 0), int(colls or 0))


async def _to_group_response(
    session: AsyncSession, group: Group
) -> GroupResponse:
    members, repos, colls = await _group_counts(session, group.id)
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        created_by=group.created_by,
        member_count=members,
        repository_grant_count=repos,
        collection_grant_count=colls,
    )


# ---------------------------------------------------------------------------
# Groups CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=GroupListResponse)
async def list_groups(
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> GroupListResponse:
    del current_admin
    rows = (await session.scalars(select(Group).order_by(Group.created_at.asc()))).all()
    items = [await _to_group_response(session, row) for row in rows]
    return GroupListResponse(items=items)


@router.post(
    "",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    payload: CreateGroupRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> GroupResponse:
    del _csrf

    group = Group(
        name=payload.name.strip(),
        description=payload.description,
        created_by=current_admin.id,
    )
    session.add(group)
    try:
        await session.flush()
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=None,
                event_type="group_created",
                metadata={"group_id": str(group.id), "name": group.name},
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "NAME_TAKEN",
            "A group with this name already exists.",
        ) from exc
    await session.refresh(group)
    return await _to_group_response(session, group)


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: UUID,
    payload: UpdateGroupRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> GroupResponse:
    del _csrf

    group = await _load_group_or_404(session, group_id)
    previous_name = group.name
    name_changed = False
    description_changed = False

    if payload.name is not None and payload.name.strip() != group.name:
        group.name = payload.name.strip()
        name_changed = True
    if payload.description is not None and payload.description != group.description:
        group.description = payload.description
        description_changed = True

    if name_changed or description_changed:
        if name_changed:
            await write_audit(
                session,
                AuditEventRecord(
                    actor_user_id=current_admin.id,
                    target_user_id=None,
                    event_type="group_renamed",
                    metadata={
                        "group_id": str(group.id),
                        "from": previous_name,
                        "to": group.name,
                    },
                ),
            )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                409,
                "NAME_TAKEN",
                "A group with this name already exists.",
            ) from exc
        await session.refresh(group)

    return await _to_group_response(session, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    group = await _load_group_or_404(session, group_id)
    name = group.name

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=None,
            event_type="group_deleted",
            metadata={"group_id": str(group.id), "name": name},
        ),
    )
    await session.delete(group)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{group_id}/members", response_model=GroupMembersResponse)
async def list_members(
    group_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> GroupMembersResponse:
    del current_admin
    await _load_group_or_404(session, group_id)
    rows = (
        await session.execute(
            select(GroupMember, User)
            .join(User, User.id == GroupMember.user_id)
            .where(GroupMember.group_id == group_id)
            .order_by(GroupMember.added_at.asc())
        )
    ).all()
    return GroupMembersResponse(
        items=[
            GroupMemberResponse(
                user_id=member.user_id,
                email=user.email,
                name=user.name,
                added_at=member.added_at,
                added_by=member.added_by,
            )
            for member, user in rows
        ]
    )


@router.post(
    "/{group_id}/members",
    response_model=AddMembersResponse,
    status_code=status.HTTP_200_OK,
)
async def add_members(
    group_id: UUID,
    payload: AddMembersRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> AddMembersResponse:
    """Idempotent bulk member-add.

    Splits the requested `user_ids` into two buckets: newly added vs.
    already members. Validates that every requested id exists in
    `users` first — a 404 on a single missing id rolls back the whole
    batch so partial state never lands. Idempotency: re-sending the
    same payload returns `added=[]` instead of a 409, which lets the
    UI resubmit the full membership list without bookkeeping.
    """
    del _csrf

    await _load_group_or_404(session, group_id)

    # Resolve requested users; bail with 404 on the first missing id.
    requested_ids = list(dict.fromkeys(payload.user_ids))  # de-dup preserve order
    existing_users = (
        await session.scalars(select(User.id).where(User.id.in_(requested_ids)))
    ).all()
    existing_set = set(existing_users)
    missing = [uid for uid in requested_ids if uid not in existing_set]
    if missing:
        raise ApiError(
            404,
            "USER_NOT_FOUND",
            f"User(s) not found: {', '.join(str(uid) for uid in missing[:5])}",
        )

    # Resolve currently-present members in this group.
    present = set(
        (
            await session.scalars(
                select(GroupMember.user_id).where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id.in_(requested_ids),
                )
            )
        ).all()
    )

    added: list[UUID] = []
    already: list[UUID] = []
    for uid in requested_ids:
        if uid in present:
            already.append(uid)
            continue
        session.add(
            GroupMember(
                group_id=group_id,
                user_id=uid,
                added_by=current_admin.id,
            )
        )
        added.append(uid)

    if added:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=None,
                event_type="group_member_added",
                metadata={
                    "group_id": str(group_id),
                    "user_ids": [str(uid) for uid in added],
                },
            ),
        )
    await session.commit()
    return AddMembersResponse(added=added, already_present=already)


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    group_id: UUID,
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    await _load_group_or_404(session, group_id)
    member = await session.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    if member is None:
        raise ApiError(404, "NOT_FOUND", "Member not found in group")

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=user_id,
            event_type="group_member_removed",
            metadata={"group_id": str(group_id)},
        ),
    )
    await session.delete(member)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Repository grants
# ---------------------------------------------------------------------------


@router.get(
    "/{group_id}/repositories",
    response_model=RepositoryGrantListResponse,
)
async def list_repository_grants(
    group_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> RepositoryGrantListResponse:
    del current_admin
    await _load_group_or_404(session, group_id)
    rows = (
        await session.execute(
            select(RepositoryGrant, Repository)
            .join(Repository, Repository.id == RepositoryGrant.repository_id)
            .where(RepositoryGrant.group_id == group_id)
            .order_by(RepositoryGrant.granted_at.asc())
        )
    ).all()
    return RepositoryGrantListResponse(
        items=[
            RepositoryGrantResponse(
                repository_id=grant.repository_id,
                repository_slug=f"{repo.host}/{repo.owner}/{repo.name}",
                level=GrantLevel(grant.level),
                granted_at=grant.granted_at,
                granted_by=grant.granted_by,
            )
            for grant, repo in rows
        ]
    )


@router.post(
    "/{group_id}/repositories",
    response_model=RepositoryGrantResponse,
    status_code=status.HTTP_200_OK,
)
async def put_repository_grant(
    group_id: UUID,
    payload: PutRepositoryGrantRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> RepositoryGrantResponse:
    """Upsert a (group, repository) grant. 200 on both create and update.

    Returns 200 instead of 201 because the semantic is "ensure this
    grant exists at this level" — the UI uses the same call to add a
    new grant or to bump a level, and a 200 doesn't have to distinguish.
    """
    del _csrf

    await _load_group_or_404(session, group_id)

    repository = await session.get(Repository, payload.repository_id)
    if repository is None or repository.deleted_at is not None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")

    grant = await session.scalar(
        select(RepositoryGrant).where(
            RepositoryGrant.group_id == group_id,
            RepositoryGrant.repository_id == payload.repository_id,
        )
    )
    is_new = grant is None
    previous_level = None if grant is None else grant.level

    if grant is None:
        grant = RepositoryGrant(
            group_id=group_id,
            repository_id=payload.repository_id,
            level=payload.level.value,
            granted_by=current_admin.id,
        )
        session.add(grant)
    else:
        grant.level = payload.level.value
        grant.granted_by = current_admin.id

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=None,
            event_type="repo_grant_added" if is_new else "repo_grant_updated",
            metadata={
                "group_id": str(group_id),
                "repository_id": str(payload.repository_id),
                "level": payload.level.value,
                **(
                    {} if is_new else {"from": previous_level}
                ),
            },
        ),
    )
    await session.commit()
    await session.refresh(grant)
    return RepositoryGrantResponse(
        repository_id=grant.repository_id,
        repository_slug=f"{repository.host}/{repository.owner}/{repository.name}",
        level=GrantLevel(grant.level),
        granted_at=grant.granted_at,
        granted_by=grant.granted_by,
    )


@router.delete(
    "/{group_id}/repositories/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_repository_grant(
    group_id: UUID,
    repository_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    await _load_group_or_404(session, group_id)
    grant = await session.scalar(
        select(RepositoryGrant).where(
            RepositoryGrant.group_id == group_id,
            RepositoryGrant.repository_id == repository_id,
        )
    )
    if grant is None:
        raise ApiError(404, "NOT_FOUND", "Repository grant not found")

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=None,
            event_type="repo_grant_removed",
            metadata={
                "group_id": str(group_id),
                "repository_id": str(repository_id),
                "level": grant.level,
            },
        ),
    )
    await session.delete(grant)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Collection grants
# ---------------------------------------------------------------------------


@router.get(
    "/{group_id}/collections",
    response_model=CollectionGrantListResponse,
)
async def list_collection_grants(
    group_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> CollectionGrantListResponse:
    del current_admin
    await _load_group_or_404(session, group_id)
    rows = (
        await session.execute(
            select(CollectionGrant, MdCollection)
            .join(MdCollection, MdCollection.id == CollectionGrant.collection_id)
            .where(CollectionGrant.group_id == group_id)
            .order_by(CollectionGrant.granted_at.asc())
        )
    ).all()
    return CollectionGrantListResponse(
        items=[
            CollectionGrantResponse(
                collection_id=grant.collection_id,
                collection_name=coll.name,
                level=GrantLevel(grant.level),
                granted_at=grant.granted_at,
                granted_by=grant.granted_by,
            )
            for grant, coll in rows
        ]
    )


@router.post(
    "/{group_id}/collections",
    response_model=CollectionGrantResponse,
    status_code=status.HTTP_200_OK,
)
async def put_collection_grant(
    group_id: UUID,
    payload: PutCollectionGrantRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> CollectionGrantResponse:
    del _csrf

    await _load_group_or_404(session, group_id)

    collection = await session.get(MdCollection, payload.collection_id)
    if collection is None:
        raise ApiError(404, "NOT_FOUND", "Collection not found")

    grant = await session.scalar(
        select(CollectionGrant).where(
            CollectionGrant.group_id == group_id,
            CollectionGrant.collection_id == payload.collection_id,
        )
    )
    is_new = grant is None
    previous_level = None if grant is None else grant.level

    if grant is None:
        grant = CollectionGrant(
            group_id=group_id,
            collection_id=payload.collection_id,
            level=payload.level.value,
            granted_by=current_admin.id,
        )
        session.add(grant)
    else:
        grant.level = payload.level.value
        grant.granted_by = current_admin.id

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=None,
            event_type=(
                "collection_grant_added" if is_new else "collection_grant_updated"
            ),
            metadata={
                "group_id": str(group_id),
                "collection_id": str(payload.collection_id),
                "level": payload.level.value,
                **({} if is_new else {"from": previous_level}),
            },
        ),
    )
    await session.commit()
    await session.refresh(grant)
    return CollectionGrantResponse(
        collection_id=grant.collection_id,
        collection_name=collection.name,
        level=GrantLevel(grant.level),
        granted_at=grant.granted_at,
        granted_by=grant.granted_by,
    )


@router.delete(
    "/{group_id}/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection_grant(
    group_id: UUID,
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    await _load_group_or_404(session, group_id)
    grant = await session.scalar(
        select(CollectionGrant).where(
            CollectionGrant.group_id == group_id,
            CollectionGrant.collection_id == collection_id,
        )
    )
    if grant is None:
        raise ApiError(404, "NOT_FOUND", "Collection grant not found")

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=None,
            event_type="collection_grant_removed",
            metadata={
                "group_id": str(group_id),
                "collection_id": str(collection_id),
                "level": grant.level,
            },
        ),
    )
    await session.delete(grant)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
