"""Idempotency key support for POST endpoints.

Stores a short-lived record keyed by `SHA256(idempotency_key + user_id)` in
the `idempotency_keys` table.  On the first request the caller creates the
row and proceeds; on a replay within the TTL the existing response payload is
returned unchanged.

TTL: 24 hours (configurable, but 24 h matches Stripe / Square convention and
fits within typical client retry windows).

Usage (inside a route handler):
    from backend.app.core.idempotency import check_or_claim, mark_complete

    record = await check_or_claim(session, key=idempotency_key, user_id=user.id)
    if record.is_replay:
        return record.payload           # JSONResponse with the original body

    # ... do the actual work ...

    await mark_complete(session, record_id=record.id, payload=response_body)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.idempotency_key import IdempotencyKey

_TTL_HOURS = 24


def _derive_key(raw_key: str, user_id: UUID) -> str:
    """Derive a stable, fixed-length hash from the raw key + user scope.

    Including the user_id prevents one user from accidentally or maliciously
    colliding with another user's idempotency key.
    """
    material = f"{user_id}:{raw_key}".encode()
    return hashlib.sha256(material).hexdigest()


class IdempotencyRecord:
    """Return value from check_or_claim."""

    def __init__(
        self,
        *,
        record_id: UUID,
        is_replay: bool,
        payload: dict | None,
    ) -> None:
        self.record_id = record_id
        self.is_replay = is_replay
        self.payload = payload


async def check_or_claim(
    session: AsyncSession,
    *,
    raw_key: str,
    user_id: UUID,
) -> IdempotencyRecord:
    """Look up or insert an idempotency record.

    - If a record with this key already exists and is within TTL → return
      is_replay=True plus the stored payload (may be None if the first request
      hasn't completed yet — caller should treat as in-flight and wait or
      return a 202 with the known id).
    - If no record (or TTL expired) → insert a new pending record and return
      is_replay=False.
    """
    key_hash = _derive_key(raw_key, user_id)
    cutoff = datetime.now(UTC) - timedelta(hours=_TTL_HOURS)

    # Purge stale records opportunistically (best-effort, no lock needed).
    await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)
    )

    existing = await session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.key_hash == key_hash)
    )
    if existing is not None:
        payload = json.loads(existing.response_payload) if existing.response_payload else None
        return IdempotencyRecord(
            record_id=existing.id,
            is_replay=True,
            payload=payload,
        )

    # First request — create a pending record.
    record = IdempotencyKey(
        id=uuid4(),
        key_hash=key_hash,
        user_id=user_id,
        response_payload=None,
    )
    session.add(record)
    await session.flush()  # Assign the row; caller commits after building response.

    return IdempotencyRecord(
        record_id=record.id,
        is_replay=False,
        payload=None,
    )


async def mark_complete(
    session: AsyncSession,
    *,
    record_id: UUID,
    payload: dict,
) -> None:
    """Store the response payload so replays can return it."""
    record = await session.get(IdempotencyKey, record_id)
    if record is not None:
        record.response_payload = json.dumps(payload)
        await session.flush()
