from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, TimestampMixin


class WikiArtifact(TimestampMixin, Base):
    """Persisted Stage 2/1.5/3 outputs of the wiki pipeline, one row per repo.

    The incremental wiki path reuses `overview` / `mindmap` / `plan` as long
    as `structural_hash`, `wiki_schema_version`, and the model ids still
    match the current run; any mismatch (or a pydantic validation error on
    rehydration) falls back to a full rebuild. The row is upserted at the
    end of every persisting wiki run, full or incremental.
    """

    __tablename__ = "wiki_artifacts"
    __table_args__ = (
        UniqueConstraint("repository_id", name="uq_wiki_artifacts_repository"),
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
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repo_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    wiki_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    structural_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    overview: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    mindmap: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    plan: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
