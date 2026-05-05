"""Admin user-management endpoints (Phase 30.1).

CRUD over the `users` table for administrators. Mounted under
`/api/admin/users` alongside the LLM-provider endpoints in
`backend/app/api/admin.py`.

Role model (Phase 30.1):
- `owner` is a singleton role enforced by `uq_users_single_owner`.
- Only the owner can promote/demote roles or transfer ownership.
- Admin-or-owner can list/create/disable/enable users.
- Owner cannot be disabled or deleted; transfer-ownership is the only
  way to move the owner bit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.core.auth import hash_password, validate_password_length
from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
    require_owner,
)
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole
from backend.app.models.user import User

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

    `password` triggers a credential reset; `role` flips owner/admin/user.
    Role transitions to or from `owner` are forbidden — use the dedicated
    transfer-owner endpoint instead.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


class DisableUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=128)


class TransferOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_email: EmailStr


class TransferOwnerResponse(BaseModel):
    previous_owner_id: UUID
    new_owner_id: UUID


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
            403,
            "FORBIDDEN_OWNER_ONLY",
            "Cannot create users with owner role; use transfer-owner instead.",
        )
    if payload.role is UserRole.ADMIN and current_admin.role is not UserRole.OWNER:
        raise ApiError(
            403,
            "FORBIDDEN_OWNER_ONLY",
            "Only the owner can promote a user to administrator.",
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
        # Role-changing requires owner; admins cannot promote / demote.
        if current_admin.role is not UserRole.OWNER:
            raise ApiError(
                403,
                "FORBIDDEN_OWNER_ONLY",
                "Only the owner can change a user's role.",
            )
        # Transitions to or from `owner` are forbidden via PATCH.
        if user.role is UserRole.OWNER or payload.role is UserRole.OWNER:
            raise ApiError(
                409,
                "OWNER_PROTECTED",
                "Use POST /admin/users/{id}/transfer-owner to move ownership.",
            )

    if (
        user.id == current_admin.id
        and payload.role is not None
        and current_admin.role is UserRole.ADMIN
        and payload.role is not UserRole.ADMIN
    ):
        # Admins cannot self-demote; owner self-changes are blocked above.
        raise ApiError(
            409,
            "SELF_DEMOTE",
            "You cannot demote yourself; ask the owner.",
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

    if user.role is UserRole.OWNER:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=current_admin.id,
                target_user_id=user.id,
                event_type="owner_disable_blocked",
                severity="critical",
            ),
        )
        await session.commit()
        raise ApiError(409, "OWNER_PROTECTED", "The owner cannot be disabled.")

    if user.id == current_admin.id:
        raise ApiError(
            409, "SELF_DISABLE", "You cannot disable your own account."
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

    if user.role is UserRole.OWNER:
        raise ApiError(
            409,
            "OWNER_PROTECTED",
            "The owner cannot be deleted.",
        )

    if user.id == current_admin.id:
        raise ApiError(
            409,
            "SELF_DELETE",
            "You cannot delete your own account; ask another admin.",
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


@router.post(
    "/{user_id}/transfer-owner",
    response_model=TransferOwnerResponse,
)
async def transfer_owner(
    user_id: UUID,
    payload: TransferOwnerRequest,
    session: AsyncSession = Depends(get_db_session),
    current_owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> TransferOwnerResponse:
    """Atomically swap the owner bit to another existing admin/user.

    The two rows are locked `FOR UPDATE`; the previous owner is demoted
    to `admin` and the target is promoted to `owner` in the same
    transaction. `uq_users_single_owner` guarantees at most one owner
    even if two transfers race.
    """
    del _csrf

    if user_id == current_owner.id:
        raise ApiError(
            409,
            "OWNER_TRANSFER_TARGET_INVALID",
            "Target user is already the owner.",
        )

    target = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if target is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    if str(payload.confirm_email).lower() != target.email.lower():
        raise ApiError(
            409,
            "OWNER_TRANSFER_TARGET_INVALID",
            "confirm_email does not match the target user's email.",
        )

    if not target.is_active:
        raise ApiError(
            409,
            "OWNER_TRANSFER_TARGET_INVALID",
            "Cannot transfer ownership to a disabled user.",
        )

    # Re-fetch the current owner under FOR UPDATE in the same transaction.
    owner_row = await session.scalar(
        select(User).where(User.id == current_owner.id).with_for_update()
    )
    if owner_row is None or owner_row.role is not UserRole.OWNER:
        raise ApiError(
            409,
            "OWNER_TRANSFER_RACE",
            "Owner row changed during transfer; retry.",
        )

    previous_owner_id = owner_row.id
    owner_row.role = UserRole.ADMIN
    # Flush so the partial unique sees one fewer owner before we promote.
    await session.flush()
    target.role = UserRole.OWNER
    await session.flush()

    # Verify exactly one owner exists post-commit.
    owner_count = await session.scalar(
        select(User.id).where(User.role == UserRole.OWNER)
    )
    if owner_count is None:
        await session.rollback()
        raise ApiError(
            500,
            "OWNER_TRANSFER_RACE",
            "Owner transfer left zero owners; retry.",
        )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=previous_owner_id,
            target_user_id=target.id,
            event_type="transfer_owner",
            metadata={"previous_owner_id": str(previous_owner_id)},
        ),
    )
    await session.commit()
    return TransferOwnerResponse(
        previous_owner_id=previous_owner_id,
        new_owner_id=target.id,
    )
