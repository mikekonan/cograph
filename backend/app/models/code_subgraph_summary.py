from __future__ import annotations

import uuid

from sqlalchemy import Double, ForeignKey, JSON, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, TimestampMixin


class _UUIDListJSON(TypeDecorator):
    """JSON column that stores list[UUID] as list[str] on non-PG dialects.

    Plain JSON can't serialize `uuid.UUID` objects (``TypeError: Object of
    type UUID is not JSON serializable``), so we stringify on write and
    revive on read. PostgreSQL bypasses this via the ARRAY(UUID) variant
    below, where native uuid objects round-trip without conversion.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return [str(v) for v in value]

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return [uuid.UUID(v) if isinstance(v, str) else v for v in value]


# JSON (with UUID bridging) on SQLite for tests; native ARRAY(UUID) on PostgreSQL.
_uuid_array_type = _UUIDListJSON().with_variant(
    postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
    "postgresql",
)


class CodeSubgraphSummary(TimestampMixin, Base):
    __tablename__ = "code_subgraph_summaries"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "root_node_id",
            name="uq_code_subgraph_summaries_repo_root",
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
    root_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_node_ids: Mapped[list[uuid.UUID]] = mapped_column(
        _uuid_array_type,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(
        Double, nullable=False, default=0.0, server_default="0.0"
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
