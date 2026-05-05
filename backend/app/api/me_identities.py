"""Per-user identity management.

Mounted under `/api/me/identities`. Lets a user inspect and unlink their
linked OIDC identities. Linking is initiated by
`POST /api/auth/oidc/{slug}/link/start` (in `auth_oidc.py`).

Unlink is gated: a user cannot unlink their last remaining auth method.
If `auth_source='oidc'` and they have no password set, they must keep at
least one identity to retain login access.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.actor import AuthenticatedActor
from backend.app.core.deps import (
    get_db_session,
    require_actor_csrf,
    require_authenticated,
)
from backend.app.core.errors import ApiError
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.user_identity import UserIdentity

router = APIRouter(prefix="/me/identities", tags=["me", "oidc"])


class IdentityView(BaseModel):
    id: UUID
    provider_id: UUID
    provider_slug: str
    provider_display_name: str
    subject: str
    email_at_link: str | None
    last_login_at: datetime | None
    created_at: datetime


class IdentityListResponse(BaseModel):
    identities: list[IdentityView]


def _to_view(identity: UserIdentity, provider: IdentityProvider) -> IdentityView:
    return IdentityView(
        id=identity.id,
        provider_id=provider.id,
        provider_slug=provider.slug,
        provider_display_name=provider.display_name,
        subject=identity.subject,
        email_at_link=identity.email_at_link,
        last_login_at=identity.last_login_at,
        created_at=identity.created_at,
    )


@router.get("", response_model=IdentityListResponse)
async def list_my_identities(
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_authenticated),
) -> IdentityListResponse:
    rows = (
        await session.execute(
            select(UserIdentity, IdentityProvider)
            .join(IdentityProvider, IdentityProvider.id == UserIdentity.provider_id)
            .where(UserIdentity.user_id == actor.user.id)
            .order_by(UserIdentity.created_at.asc())
        )
    ).all()
    return IdentityListResponse(
        identities=[_to_view(identity, provider) for identity, provider in rows]
    )


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_my_identity(
    identity_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> None:
    identity = await session.get(UserIdentity, identity_id)
    if identity is None or identity.user_id != actor.user.id:
        raise ApiError(404, "NOT_FOUND", "Identity not found")

    user = actor.user
    if user.auth_source == "oidc" and user.password_hash is None:
        # User has no password — must keep at least one identity.
        remaining = await session.scalar(
            select(func.count(UserIdentity.id)).where(
                UserIdentity.user_id == user.id,
                UserIdentity.id != identity.id,
            )
        )
        if not remaining:
            raise ApiError(
                409,
                "LAST_AUTH_METHOD",
                "Cannot unlink the only remaining auth method. "
                "Set a password first.",
            )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=user.id,
            target_user_id=user.id,
            event_type="user_identity_unlinked",
            metadata={
                "identity_id": str(identity.id),
                "provider_id": str(identity.provider_id),
                "subject": identity.subject,
            },
        ),
    )
    await session.delete(identity)
    await session.commit()
    return None
