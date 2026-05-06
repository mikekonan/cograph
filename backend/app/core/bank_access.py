from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import ApiError
from backend.app.models.bank import Bank
from backend.app.models.enums import UserRole
from backend.app.models.user import User


def can_read_bank(*, bank: Bank, current_user: User) -> bool:
    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return True
    return bank.owner_id == current_user.id


async def get_readable_bank(
    *,
    session: AsyncSession,
    bank_id: UUID,
    current_user: User | None,
) -> Bank:
    bank = await session.get(Bank, bank_id)
    if bank is None:
        raise ApiError(404, "NOT_FOUND", "Bank not found")
    if current_user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    if can_read_bank(bank=bank, current_user=current_user):
        return bank
    raise ApiError(403, "FORBIDDEN", "Bank access denied")


async def ensure_readable_banks(
    *,
    session: AsyncSession,
    bank_ids: Iterable[UUID] | None,
    current_user: User | None,
) -> None:
    if not bank_ids:
        return
    seen: set[UUID] = set()
    for bank_id in bank_ids:
        if bank_id in seen:
            continue
        seen.add(bank_id)
        await get_readable_bank(
            session=session,
            bank_id=bank_id,
            current_user=current_user,
        )
