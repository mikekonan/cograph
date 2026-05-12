"""Map OIDC ``groups`` claim → cograph group membership.

Additive sync. On every successful OIDC login the user is added to
every cograph group whose ``(oidc_provider_id, oidc_group_name)`` pair
matches a claim in the ID token. Never removes — manual members
coexist; if an IdP-side group disappears from a user's claims, their
cograph membership stays. Deprovisioning is a follow-up if/when
operators ask for it.

Idempotent: uses ``INSERT ... ON CONFLICT DO NOTHING`` on the
composite primary key ``(group_id, user_id)``. ``GroupMember.source``
is set to ``'oidc'`` on synced rows so the admin UI can show
provenance.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.group import Group, GroupMember
from backend.app.models.identity_provider import IdentityProvider

_log = logging.getLogger(__name__)


async def sync_oidc_group_memberships(
    *,
    session: AsyncSession,
    user_id: UUID,
    provider: IdentityProvider,
    claim_groups: Iterable[str] | None,
) -> list[UUID]:
    """Add ``user_id`` to every cograph group whose ``(provider, name)``
    pair matches a claim.

    Returns the list of ``group_id`` values the user was actually
    inserted into (excludes rows that already existed). Caller commits.

    Idempotent: existence is checked per-group before insert so
    re-running the sync for the same login is a no-op and existing
    rows (especially manual ones) are never touched. We chose this
    over an INSERT ... ON CONFLICT to keep the path dialect-agnostic
    (the test suite runs on SQLite while prod is PG).
    """

    if not claim_groups:
        return []

    claim_set = {g for g in claim_groups if g}
    if not claim_set:
        return []

    matched_group_ids: list[UUID] = list(
        (
            await session.scalars(
                select(Group.id)
                .where(Group.oidc_provider_id == provider.id)
                .where(Group.oidc_group_name.in_(claim_set))
            )
        ).all()
    )
    if not matched_group_ids:
        return []

    already_member: set[UUID] = set(
        (
            await session.scalars(
                select(GroupMember.group_id)
                .where(GroupMember.user_id == user_id)
                .where(GroupMember.group_id.in_(matched_group_ids))
            )
        ).all()
    )

    inserted: list[UUID] = []
    for gid in matched_group_ids:
        if gid in already_member:
            continue
        session.add(
            GroupMember(group_id=gid, user_id=user_id, source="oidc")
        )
        inserted.append(gid)

    if inserted:
        await session.flush()
        _log.info(
            "oidc_group_sync: user=%s provider=%s added=%s",
            user_id,
            provider.slug,
            len(inserted),
        )

    return inserted
