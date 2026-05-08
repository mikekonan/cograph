"""Admin user-management endpoints.

CRUD over the `users` table for administrators. Mounted under
`/api/admin/users` alongside the LLM-provider endpoints in
`backend/app/api/admin.py`.

Role model: OWNER and ADMIN share the same privilege tier. OWNER is a
label set at instance bootstrap and is not transferable through the
API — role transitions to or from OWNER are rejected. Disabling or
deleting the last user with admin/owner role is rejected to keep the
instance reachable; SCIM enforces the same invariant separately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.core.auth import hash_password, validate_password_length
from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
)
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole
from backend.app.models.user import User

_ADMIN_ROLES = (UserRole.OWNER, UserRole.ADMIN)


async def _would_leave_no_admins(session: AsyncSession, user: User) -> bool:
    """True iff disabling/demoting `user` would leave zero active admin/owner."""
    if user.role not in _ADMIN_ROLES:
        return False
    remaining = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.id != user.id,
            User.role.in_(_ADMIN_ROLES),
            User.is_active.is_(True),
        )
    )
    return (remaining or 0) == 0

router = APIRouter(prefix="/admin/users", tags=["admin", "users"])


class AdminUserResponse(BaseModel):
    id: UUID
    email: str
    name: str | None
    role: UserRole
    is_active: bool
    auth_source: str
    deactivated_at: datetime | None
    deactivated_reason: str | None
    last_login_at: datetime | None
    created_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    role: UserRole = UserRole.USER


class UpdateUserRequest(BaseModel):
    """Patch shape — every field optional, server applies what's set.

    `password` triggers a credential reset; `role` flips between admin
    and user. Transitions to or from `owner` are rejected (owner is a
    label set only at instance bootstrap).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


class DisableUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=128)


def _to_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        auth_source=user.auth_source,
        deactivated_at=user.deactivated_at,
        deactivated_reason=user.deactivated_reason,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> AdminUserListResponse:
    del current_admin
    rows = (
        await session.scalars(select(User).order_by(User.created_at.asc()))
    ).all()
    return AdminUserListResponse(items=[_to_response(row) for row in rows])


@router.post(
    "",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: CreateUserRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> AdminUserResponse:
    del _csrf

    if payload.role is UserRole.OWNER:
        raise ApiError(
            409,
            "OWNER_LABEL_LOCKED",
            "Owner is set only at instance bootstrap; cannot be assigned via API.",
        )

    try:
        validate_password_length(payload.password)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_FAILED", str(exc)) from exc

    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        name=payload.name,
        role=payload.role,
        auth_source="password",
    )
    session.add(user)
    try:
        await session.flush()
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=user.id,
                event_type="user_created",
                metadata={"role": user.role.value},
            ),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "EMAIL_TAKEN",
            "A user with this email already exists.",
        ) from exc
    await session.refresh(user)
    return _to_response(user)


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> AdminUserResponse:
    del _csrf

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    role_change = payload.role is not None and payload.role is not user.role

    if role_change:
        # Owner label is bootstrap-only; transitions to/from owner are rejected.
        if user.role is UserRole.OWNER or payload.role is UserRole.OWNER:
            raise ApiError(
                409,
                "OWNER_LABEL_LOCKED",
                "Owner role is set at instance bootstrap and cannot be changed via API.",
            )
        # Demoting yourself out of admin is allowed only if another admin remains.
        if (
            user.id == current_admin.id
            and payload.role is UserRole.USER
            and await _would_leave_no_admins(session, user)
        ):
            raise ApiError(
                409,
                "LAST_ADMIN_PROTECTED",
                "Cannot demote the last administrator; promote another admin first.",
            )

    if payload.password is not None:
        try:
            validate_password_length(payload.password)
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_FAILED", str(exc)) from exc
        user.password_hash = hash_password(payload.password)
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=user.id,
                event_type="password_set",
            ),
        )

    if payload.name is not None:
        user.name = payload.name

    if role_change and payload.role is not None:
        previous_role = user.role
        user.role = payload.role
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=user.id,
                event_type="role_changed",
                metadata={"from": previous_role.value, "to": payload.role.value},
            ),
        )

    await session.commit()
    await session.refresh(user)
    return _to_response(user)


@router.post("/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: UUID,
    payload: DisableUserRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    if user.id == current_admin.id:
        raise ApiError(
            409, "SELF_DISABLE", "You cannot disable your own account."
        )

    if await _would_leave_no_admins(session, user):
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=user.id,
                event_type="last_admin_disable_blocked",
                severity="critical",
            ),
        )
        await session.commit()
        raise ApiError(
            409,
            "LAST_ADMIN_PROTECTED",
            "Cannot disable the last administrator.",
        )

    if not user.is_active:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    user.deactivated_reason = payload.reason or "admin"
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=user.id,
            event_type="user_disabled",
            metadata={"reason": user.deactivated_reason},
        ),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    if user.is_active:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    user.is_active = True
    user.deactivated_at = None
    user.deactivated_reason = None
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=user.id,
            event_type="user_enabled",
        ),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    if user.id == current_admin.id:
        raise ApiError(
            409,
            "SELF_DELETE",
            "You cannot delete your own account; ask another admin.",
        )

    if await _would_leave_no_admins(session, user):
        raise ApiError(
            409,
            "LAST_ADMIN_PROTECTED",
            "Cannot delete the last administrator.",
        )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=user.id,
            event_type="user_deleted",
            metadata={"email": user.email},
        ),
    )
    await session.delete(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
