"""Canonical SCIM payload hashing + idempotency-key construction.

The unique constraint on `scim_events.idempotency_key` is the only thing
standing between us and a duplicate-cascade bug. Two retries arriving
30 s apart with the same payload must produce the same key — so the
hash must be stable under JSON key reordering and whitespace
differences.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID


def canonical_payload_hash(payload: dict[str, Any] | list[Any] | None) -> bytes:
    """Stable SHA-256 over a SCIM payload.

    `sort_keys=True` + tight separators normalises key order and
    whitespace. None payloads (e.g. DELETE) hash a literal `null`.
    """

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).digest()


def build_idempotency_key(
    *,
    provider_id: UUID,
    external_id: str | None,
    operation: str,
    payload_hash: bytes,
) -> str:
    """Mirror the spec: `provider:external|-:op:hex(payload_hash)`."""

    return (
        f"{provider_id}:"
        f"{external_id or '-'}:"
        f"{operation}:"
        f"{payload_hash.hex()}"
    )
