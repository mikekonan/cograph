from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.audit_event import AuditEvent

AuditSeverity = Literal["info", "warning", "critical"]


@dataclass(slots=True)
class AuditEventRecord:
    """In-memory shape used by call sites; persisted via `write_audit`."""

    actor_user_id: UUID | None
    target_user_id: UUID | None
    event_type: str
    severity: AuditSeverity = "info"
    metadata: dict[str, Any] = field(default_factory=dict)


async def write_audit(session: AsyncSession, record: AuditEventRecord) -> AuditEvent:
    """Insert one audit row in the caller's transaction.

    Caller is responsible for committing the surrounding transaction.
    Audit writes are part of the privileged action; they must roll back
    together if the action fails.
    """
    row = AuditEvent(
        actor_user_id=record.actor_user_id,
        target_user_id=record.target_user_id,
        event_type=record.event_type,
        severity=record.severity,
        metadata_json=record.metadata,
    )
    session.add(row)
    await session.flush()
    return row
