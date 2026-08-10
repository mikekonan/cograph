"""SCIM bearer-token resolver (Phase 30.4).

The SCIM v2 router authenticates via raw bearer tokens minted at
`/api/admin/scim-clients`. Tokens use the same plaintext shape as PATs
(`cgr_pat_<48>`) and the same hash strategy (raw SHA-256), but live in
their own table — `scim_clients` — and are never minted via
`/me/tokens`.

Bearer-only: SCIM is exempt from CSRF and from cookie auth. There is no
fallback to JWT or PAT.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.scim_client import SCIMClient


def hash_scim_token(plaintext: str) -> bytes:
    """SHA-256 of the bearer plaintext — matches the `token_hash` column."""

    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def resolve_scim_client(
    token: str,
    *,
    session: AsyncSession,
    client_ip: str | None = None,
    require_scope: str = "users:write",
) -> SCIMClient | None:
    """Look up an active SCIM client by its bearer plaintext.

    Returns None if the token does not match an active row, the client's
    provider is disabled, or the client is missing the required scope.
    Side-effect: bumps `last_used_at` and `last_used_ip` on success.
    """

    digest = hash_scim_token(token)
    row = (
        await session.execute(
            select(SCIMClient)
            .options(selectinload(SCIMClient.provider))
            .where(
                SCIMClient.token_hash == digest,
                SCIMClient.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    provider: IdentityProvider | None = row.provider
    if provider is None or not provider.enabled:
        return None
    if require_scope not in (row.scopes or []):
        return None

    row.last_used_at = datetime.now(UTC)
    if client_ip:
        row.last_used_ip = client_ip[:64]
    return row
