from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base


class CodeEdge(Base):
    __tablename__ = "code_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_node_id",
            "edge_type",
            "target_qualified_name",
            name="uq_code_edges_source_type_target",
        ),
        CheckConstraint(
            "edge_type IN ('calls', 'inherits', 'imports', 'declares')",
            name="ck_code_edges_edge_type",
        ),
        Index("idx_code_edges_source", "source_node_id", "edge_type"),
        Index(
            "idx_code_edges_target",
            "target_node_id",
            "edge_type",
            postgresql_where="target_node_id IS NOT NULL",
        ),
        Index(
            "idx_code_edges_unresolved",
            "repository_id",
            "target_qualified_name",
            postgresql_where="target_node_id IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="SET NULL"),
    )
    target_qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    edge_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
