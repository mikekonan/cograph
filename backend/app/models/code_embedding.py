from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, CreatedAtMixin
from backend.app.db.vector_type import VectorType


class CodeEmbedding(CreatedAtMixin, Base):
    """Stores graph-enriched embedding vectors for code_nodes.

    One row per code_node. content_hash mirrors code_node.content_hash so the
    embedder can skip unchanged nodes without re-fetching the vector.
    """

    __tablename__ = "code_embeddings"
    __table_args__ = (
        UniqueConstraint("code_node_id", name="uq_code_embeddings_node"),
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
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(1536))
    model: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    neighbor_hash: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
