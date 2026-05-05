from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, TimestampMixin


class SourceFile(TimestampMixin, Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "file_path", name="uq_source_files_repo_file_path"),
        Index("idx_source_files_repo_hash", "repository_id", "content_hash"),
        CheckConstraint("kind IN ('code', 'markdown', 'other')", name="ck_source_files_kind"),
        CheckConstraint("bytes >= 0", name="ck_source_files_bytes"),
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
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    blob_hash: Mapped[str | None] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(Text)
