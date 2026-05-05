from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, LargeBinary, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, CreatedAtMixin


class ModuleEmbedding(CreatedAtMixin, Base):
    __tablename__ = "module_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "module_node_id",
            name="uq_module_embeddings_repo_module",
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
    module_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("code_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    model: Mapped[str] = mapped_column(Text, nullable=False)
