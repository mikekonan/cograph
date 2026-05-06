"""Personal Access Tokens — unified REST + MCP authentication (Phase 30.2).

Endpoints under `/api/me/tokens` let any authenticated user mint, list,
revoke and rotate their own tokens. The same opaque-token format
authenticates both REST and MCP transports — see `_resolve_pat` in
`backend.app.core.deps`.

Security model:
- Plaintext is shown ONCE in the create / rotate response. The DB stores
  raw `sha256(token)` (binary 32 bytes) — uninvertible at the 288-bit
  secret length, so no pepper or HMAC is needed.
- Revocation is *soft*: `revoked_at` + `revoked_reason` are set so audit
  can attribute future failed-auth attempts. The row stays for
  forensics; rotation = revoke(old, reason='rotation') + mint(new).
- Scopes are a closed set: `api:read`, `api:write`, `mcp`. The DB
  CHECK constraint mirrors this.
- PAT actors are forbidden from minting / rotating PATs (avoids token
  laundering loops).
"""

from __future__ import annotations

import hashlib
import secrets as stdlib_secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.actor import AuthenticatedActor
from backend.app.core.deps import (
    PAT_PLAINTEXT_PREFIX,
    PAT_PREFIX_DISPLAY_CHARS,
    get_db_session,
    require_actor_csrf,
    require_admin_or_owner,
    require_csrf,
    require_scope,
)
from backend.app.core.errors import ApiError
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.user import User

router = APIRouter(tags=["tokens"])


# Token format: cgr_pat_<48 random base64url bytes>. 36 raw bytes →
# ~48 base64url chars; combined with the `cgr_pat_` literal the secret
# is well above 256 bits of entropy (uninvertible vs SHA-256).
_TOKEN_RAW_BYTES = 36

_ALLOWED_SCOPES: frozenset[str] = frozenset({"api:read", "api:write", "mcp"})

# Soft-revoke `revoked_reason` values mirrored by the DB CHECK constraint.
_VALID_REVOKE_REASONS: frozenset[str] = frozenset(
    {"user", "rotation", "admin", "idp_block", "role_change"}
)


def _hash_token(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


def _generate_token() -> tuple[str, bytes, str]:
    """Returns (plaintext, sha256_digest, display_prefix)."""

    secret = stdlib_secrets.token_urlsafe(_TOKEN_RAW_BYTES)
    plaintext = f"{PAT_PLAINTEXT_PREFIX}{secret}"
    return plaintext, _hash_token(plaintext), plaintext[:PAT_PREFIX_DISPLAY_CHARS]


class TokenView(BaseModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: str | None
    last_used_at: datetime | None
    last_used_ip: str | None
    created_at: datetime


class TokenListResponse(BaseModel):
    tokens: list[TokenView]


class CreateTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            scope = raw.strip()
            if not scope:
                continue
            if scope not in _ALLOWED_SCOPES:
                raise ValueError(
                    f"Unknown scope '{scope}'. Allowed: {sorted(_ALLOWED_SCOPES)}"
                )
            if scope in seen:
                continue
            seen.add(scope)
            cleaned.append(scope)
        if not cleaned:
            raise ValueError("scopes must contain at least one entry")
        return cleaned


class CreateTokenResponse(BaseModel):
    """Plaintext returned exactly once — at create or rotate."""

    token: str
    view: TokenView


class RevokeAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="admin", max_length=32)

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str) -> str:
        if value not in _VALID_REVOKE_REASONS:
            raise ValueError(
                f"Unknown reason '{value}'. Allowed: {sorted(_VALID_REVOKE_REASONS)}"
            )
        return value


class RevokeAllResponse(BaseModel):
    revoked_count: int


def _to_view(row: PersonalAccessToken) -> TokenView:
    return TokenView(
        id=row.id,
        name=row.name,
        prefix=row.token_prefix,
        scopes=list(row.scopes),
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
        last_used_at=row.last_used_at,
        last_used_ip=row.last_used_ip,
        created_at=row.created_at,
    )


def _reject_pat_self_mint(actor: AuthenticatedActor) -> None:
    """PAT actors cannot mint or rotate further PATs.

    Token-laundering guard: a PAT is bound to its declared scopes, and
    minting new tokens should always require a human session.
    """
    if actor.method == "pat":
        raise ApiError(
            403,
            "FORBIDDEN_PAT_SELF_MINT",
            "Personal access tokens cannot mint other tokens. "
            "Sign in with your account to manage tokens.",
        )


@router.get("/me/tokens", response_model=TokenListResponse)
async def list_my_tokens(
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_scope("api:read")),
) -> TokenListResponse:
    rows = (
        await session.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == actor.user.id)
            .order_by(PersonalAccessToken.created_at.asc())
        )
    ).all()
    return TokenListResponse(tokens=[_to_view(row) for row in rows])


@router.post(
    "/me/tokens",
    response_model=CreateTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_token(
    payload: CreateTokenRequest,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> CreateTokenResponse:
    _reject_pat_self_mint(actor)

    name = payload.name.strip()
    if not name:
        raise ApiError(422, "VALIDATION_FAILED", "Name cannot be blank")

    expires_at = payload.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "expires_at must be in the future",
            )

    plaintext, token_hash, prefix = _generate_token()
    row = PersonalAccessToken(
        user_id=actor.user.id,
        name=name,
        token_hash=token_hash,
        token_prefix=prefix,
        scopes=list(payload.scopes),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.user.id,
            target_user_id=actor.user.id,
            event_type="pat_minted",
            metadata={
                "token_id": str(row.id),
                "scopes": list(row.scopes),
                "name": row.name,
            },
        ),
    )
    await session.commit()
    await session.refresh(row)

    return CreateTokenResponse(token=plaintext, view=_to_view(row))


@router.delete("/me/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_token(
    token_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> None:
    row = await session.get(PersonalAccessToken, token_id)
    if row is None or row.user_id != actor.user.id:
        raise ApiError(404, "NOT_FOUND", "Token not found")
    if row.revoked_at is not None:
        return None  # idempotent

    row.revoked_at = datetime.now(UTC)
    row.revoked_reason = "user"
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.user.id,
            target_user_id=actor.user.id,
            event_type="pat_revoked",
            metadata={"token_id": str(row.id), "reason": "user"},
        ),
    )
    await session.commit()
    return None


@router.post(
    "/me/tokens/{token_id}/rotate",
    response_model=CreateTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_my_token(
    token_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> CreateTokenResponse:
    """Atomically revoke an existing token and mint a replacement.

    The new token inherits `name`, `scopes`, and `expires_at` from the
    old one. The old row is left in place with `revoked_reason='rotation'`
    so historical audit lines remain attributable.
    """
    _reject_pat_self_mint(actor)

    old = await session.get(PersonalAccessToken, token_id)
    if old is None or old.user_id != actor.user.id:
        raise ApiError(404, "NOT_FOUND", "Token not found")
    if old.revoked_at is not None:
        raise ApiError(
            409,
            "TOKEN_ALREADY_REVOKED",
            "This token has been revoked; mint a new one instead.",
        )

    plaintext, token_hash, prefix = _generate_token()
    new_row = PersonalAccessToken(
        user_id=actor.user.id,
        name=old.name,
        token_hash=token_hash,
        token_prefix=prefix,
        scopes=list(old.scopes),
        expires_at=old.expires_at,
    )
    session.add(new_row)

    old.revoked_at = datetime.now(UTC)
    old.revoked_reason = "rotation"

    await session.flush()
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=actor.user.id,
            target_user_id=actor.user.id,
            event_type="pat_rotated",
            metadata={
                "previous_token_id": str(old.id),
                "new_token_id": str(new_row.id),
                "scopes": list(new_row.scopes),
            },
        ),
    )
    await session.commit()
    await session.refresh(new_row)

    return CreateTokenResponse(token=plaintext, view=_to_view(new_row))


# ---------------------------------------------------------------------------
# Admin surface
# ---------------------------------------------------------------------------


@router.get(
    "/admin/users/{user_id}/tokens",
    response_model=TokenListResponse,
)
async def admin_list_user_tokens(
    user_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
) -> TokenListResponse:
    del current_admin

    target = await session.get(User, user_id)
    if target is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    rows = (
        await session.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.asc())
        )
    ).all()
    return TokenListResponse(tokens=[_to_view(row) for row in rows])


@router.post(
    "/admin/users/{user_id}/tokens/revoke-all",
    response_model=RevokeAllResponse,
)
async def admin_revoke_all_tokens(
    user_id: UUID,
    payload: RevokeAllRequest,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> RevokeAllResponse:
    """Mass-revoke every active PAT for a target user.

    Used by the IdP-block flow (Phase 30.4 SCIM) and as an admin escape
    hatch. Idempotent — already-revoked rows are skipped, the count
    returned reflects only the rows transitioned by this call.
    """
    del _csrf

    target = await session.get(User, user_id)
    if target is None:
        raise ApiError(404, "NOT_FOUND", "User not found")

    rows = (
        await session.scalars(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
        )
    ).all()

    now = datetime.now(UTC)
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = payload.reason

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=current_admin.id,
            target_user_id=user_id,
            event_type="pats_revoked_all",
            severity="warning",
            metadata={
                "reason": payload.reason,
                "revoked_count": len(rows),
                "token_ids": [str(row.id) for row in rows],
            },
        ),
    )
    await session.commit()
    return RevokeAllResponse(revoked_count=len(rows))
