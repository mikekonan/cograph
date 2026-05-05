from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class RepoDocumentChunkMention(Base):
    __tablename__ = "repo_document_chunk_mentions"
    __table_args__ = (
        Index("idx_repo_document_chunk_mentions_node", "code_node_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repo_document_chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    code_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
