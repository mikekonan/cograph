from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class McpOperatorBriefing(Base):
    """Singleton row holding the deployment-wide MCP operator briefing.

    Locked to `id=1` by a CHECK constraint at the DB layer so any
    accidental second insert errors loudly. Empty `content` is valid
    — the rendered playbook then falls back to the in-code default.
    """

    __tablename__ = "mcp_operator_briefing"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
