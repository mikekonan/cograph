from __future__ import annotations

import uuid

from sqlalchemy import Double, ForeignKey, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, TimestampMixin


class CodeNodeSummary(TimestampMixin, Base):
    __tablename__ = "code_node_summaries"
    __table_args__ = (
        UniqueConstraint("code_node_id", name="uq_code_node_summaries_code_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    code_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(
        Double, nullable=False, default=0.0, server_default="0.0"
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    neighbor_hash: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
