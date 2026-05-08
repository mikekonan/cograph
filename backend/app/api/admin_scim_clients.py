"""Owner-managed SCIM client tokens (Phase 30.4).

Mounted under `/api/admin/scim-clients`. Only an owner can mint, rotate
or revoke tokens; admins can read the listing for diagnostics. Plaintext
bearer tokens are returned exactly once at create / rotate.

Companion read-only endpoint `GET /api/admin/scim-events` exposes the
per-request audit feed (sortable by client, target user, status, applied_at).
"""

from __future__ import annotations

import secrets as stdlib_secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.scim_resolver import hash_scim_token
from backend.app.core.deps import (
    PAT_PLAINTEXT_PREFIX,
    PAT_PREFIX_DISPLAY_CHARS,
    get_db_session,
    require_admin_or_owner,
)
from backend.app.core.errors import ApiError
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.scim_client import SCIMClient
from backend.app.models.scim_event import SCIMEvent
from backend.app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin", "scim"])


# Bearer plaintext shape mirrors PATs (`cgr_pat_<48 base64url>`) — the
# DB store is identical (raw SHA-256, soft revoke). Different table,
# different audit lineage.
_TOKEN_RAW_BYTES = 36


def _generate_token() -> tuple[str, bytes, str]:
    secret = stdlib_secrets.token_urlsafe(_TOKEN_RAW_BYTES)
    plaintext = f"{PAT_PLAINTEXT_PREFIX}{secret}"
    return (
        plaintext,
        hash_scim_token(plaintext),
        plaintext[:PAT_PREFIX_DISPLAY_CHARS],
    )


# ---------------------------------------------------------------------------
# Pydantic views
# ---------------------------------------------------------------------------


class SCIMClientView(BaseModel):
    id: UUID
    provider_id: UUID
    provider_slug: str | None
    name: str
    token_prefix: str
    scopes: list[str]
    revoked_at: datetime | None
    revoked_reason: str | None
    last_used_at: datetime | None
    last_used_ip: str | None
    created_at: datetime


class SCIMClientListResponse(BaseModel):
    clients: list[SCIMClientView]


class CreateSCIMClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = None


class SCIMClientCreated(BaseModel):
    """Plaintext returned exactly once — at create or rotate."""

    token: str
    view: SCIMClientView


class SCIMEventView(BaseModel):
    id: UUID
    client_id: UUID
    provider_id: UUID
    operation: Literal["create", "replace", "patch", "delete"]
    external_id: str | None
    target_user_id: UUID | None
    status: Literal["applied", "no_op", "rejected"]
    error_code: str | None
    applied_at: datetime


class SCIMEventListResponse(BaseModel):
    events: list[SCIMEventView]


def _to_view(row: SCIMClient) -> SCIMClientView:
    provider = row.provider
    return SCIMClientView(
        id=row.id,
        provider_id=row.provider_id,
        provider_slug=provider.slug if provider else None,
        name=row.name,
        token_prefix=row.token_prefix,
        scopes=list(row.scopes or []),
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
        last_used_at=row.last_used_at,
        last_used_ip=row.last_used_ip,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/scim-clients", response_model=SCIMClientListResponse)
async def list_scim_clients(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_admin_or_owner),
) -> SCIMClientListResponse:
    rows = (
        await session.execute(
            select(SCIMClient).order_by(SCIMClient.created_at.desc())
        )
    ).scalars().all()
    return SCIMClientListResponse(clients=[_to_view(r) for r in rows])


@router.post(
    "/scim-clients",
    response_model=SCIMClientCreated,
    status_code=201,
)
async def create_scim_client(
    body: CreateSCIMClientRequest,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> SCIMClientCreated:
    provider = await session.get(IdentityProvider, body.provider_id)
    if provider is None:
        raise ApiError(404, "IDP_NOT_FOUND", "Identity provider not found")

    plaintext, token_hash, prefix = _generate_token()
    row = SCIMClient(
        provider_id=provider.id,
        name=body.name.strip(),
        token_hash=token_hash,
        token_prefix=prefix,
        scopes=body.scopes or ["users:write"],
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.id,
            target_user_id=None,
            event_type="scim_client_created",
            metadata={
                "client_id": str(row.id),
                "provider_id": str(provider.id),
                "name": row.name,
            },
        ),
    )
    await session.commit()
    await session.refresh(row, attribute_names=["provider"])
    return SCIMClientCreated(token=plaintext, view=_to_view(row))


@router.delete("/scim-clients/{client_id}", status_code=204)
async def revoke_scim_client(
    client_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> None:
    row = await session.get(SCIMClient, client_id)
    if row is None or row.revoked_at is not None:
        raise ApiError(404, "SCIM_CLIENT_NOT_FOUND", "SCIM client not found")
    row.revoked_at = datetime.now(UTC)
    row.revoked_reason = "admin"
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.id,
            target_user_id=None,
            event_type="scim_client_revoked",
            metadata={"client_id": str(row.id), "reason": "admin"},
        ),
    )
    await session.commit()
    return None


@router.post(
    "/scim-clients/{client_id}/rotate",
    response_model=SCIMClientCreated,
    status_code=201,
)
async def rotate_scim_client(
    client_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> SCIMClientCreated:
    old = await session.get(SCIMClient, client_id)
    if old is None or old.revoked_at is not None:
        raise ApiError(404, "SCIM_CLIENT_NOT_FOUND", "SCIM client not found")
    old.revoked_at = datetime.now(UTC)
    old.revoked_reason = "rotation"

    plaintext, token_hash, prefix = _generate_token()
    new_row = SCIMClient(
        provider_id=old.provider_id,
        name=old.name,
        token_hash=token_hash,
        token_prefix=prefix,
        scopes=list(old.scopes or ["users:write"]),
    )
    session.add(new_row)
    await session.flush()
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.id,
            target_user_id=None,
            event_type="scim_client_rotated",
            metadata={
                "previous_client_id": str(old.id),
                "client_id": str(new_row.id),
                "provider_id": str(old.provider_id),
            },
        ),
    )
    await session.commit()
    await session.refresh(new_row, attribute_names=["provider"])
    return SCIMClientCreated(token=plaintext, view=_to_view(new_row))


@router.get("/scim-events", response_model=SCIMEventListResponse)
async def list_scim_events(
    client_id: UUID | None = Query(default=None),
    target_user_id: UUID | None = Query(default=None),
    status: Literal["applied", "no_op", "rejected"] | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_admin_or_owner),
) -> SCIMEventListResponse:
    stmt = select(SCIMEvent).order_by(desc(SCIMEvent.applied_at)).limit(limit)
    if client_id is not None:
        stmt = stmt.where(SCIMEvent.client_id == client_id)
    if target_user_id is not None:
        stmt = stmt.where(SCIMEvent.target_user_id == target_user_id)
    if status is not None:
        stmt = stmt.where(SCIMEvent.status == status)
    if since is not None:
        stmt = stmt.where(SCIMEvent.applied_at >= since)
    rows = (await session.execute(stmt)).scalars().all()
    return SCIMEventListResponse(
        events=[
            SCIMEventView(
                id=row.id,
                client_id=row.client_id,
                provider_id=row.provider_id,
                operation=row.operation,  # type: ignore[arg-type]
                external_id=row.external_id,
                target_user_id=row.target_user_id,
                status=row.status,  # type: ignore[arg-type]
                error_code=row.error_code,
                applied_at=row.applied_at,
            )
            for row in rows
        ]
    )
