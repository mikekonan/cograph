"""Single-transaction deprovisioning cascade (Phase 30.4).

When SCIM marks a user inactive, every credential they hold dies inside
one `BEGIN…COMMIT`:

1. `users.is_active = false`, `deactivated_at = now()`,
   `deactivated_reason = 'scim'`.
2. All non-revoked PATs flipped to `revoked_reason='idp_block'`.
3. Refresh-token families dropped (forces re-login).
4. Audit row written with `event_type='scim_user_disabled'`.

Cookie sessions die at the next request — Layer-1 enforcement reads
`is_active` per call (no per-process cache).

Owner protection: SCIM never disables the owner. The handler raises
`SCIMOwnerProtectedError`, which the router translates to a 403 SCIM
error AND records an `applied_at` row in `scim_events` with
`status='rejected'`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.models.enums import UserRole
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.refresh_token_family import RefreshTokenFamily
from backend.app.models.scim_client import SCIMClient
from backend.app.models.user import User


class SCIMOwnerProtectedError(Exception):
    """SCIM tried to disable an owner — rejected, audited as critical."""


async def disable_user_cascade(
    *,
    target: User,
    actor_client: SCIMClient,
    external_id: str | None,
    session: AsyncSession,
) -> None:
    """Atomic deprovisioning. Caller commits the surrounding transaction."""

    if target.role is UserRole.OWNER:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=None,
                target_user_id=target.id,
                event_type="scim_owner_disable_blocked",
                severity="critical",
                metadata={
                    "client_id": str(actor_client.id),
                    "external_id": external_id,
                },
            ),
        )
        raise SCIMOwnerProtectedError()

    if not target.is_active:
        # Idempotent no-op: caller still records the SCIM event (no_op)
        # but we don't re-write audit / re-revoke tokens.
        return

    target.is_active = False
    target.deactivated_at = datetime.now(UTC)
    target.deactivated_reason = "scim"

    await session.execute(
        update(PersonalAccessToken)
        .where(
            PersonalAccessToken.user_id == target.id,
            PersonalAccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now(), revoked_reason="idp_block")
    )

    await session.execute(
        delete(RefreshTokenFamily).where(RefreshTokenFamily.user_id == target.id)
    )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=None,
            target_user_id=target.id,
            event_type="scim_user_disabled",
            metadata={
                "client_id": str(actor_client.id),
                "external_id": external_id,
            },
        ),
    )


async def enable_user(
    *,
    target: User,
    actor_user_id: UUID | None,
    actor_client: SCIMClient | None,
    session: AsyncSession,
) -> None:
    """Flip `is_active` back on after a prior SCIM disable.

    PATs stay revoked — re-enable is intentionally narrow.  Owners can
    re-enable from the admin UI; SCIM PUT `active=true` flows through
    the same helper.
    """

    if target.is_active:
        return

    target.is_active = True
    target.deactivated_at = None
    target.deactivated_reason = None

    metadata: dict[str, str | None] = {}
    if actor_client is not None:
        metadata["client_id"] = str(actor_client.id)

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor_user_id,
            target_user_id=target.id,
            event_type="scim_user_enabled",
            metadata=metadata,
        ),
    )
