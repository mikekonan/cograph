from __future__ import annotations

import uuid

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import uuid7
from backend.app.db.base import Base, TimestampMixin


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("repository_id", "slug", name="uq_documents_repo_slug"),
        Index("idx_documents_repository_sort", "repository_id", "sort_order"),
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
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    source_node_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_repo_doc_chunk_ids: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    citations: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    quality: Mapped[dict[str, object] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    # Incremental-wiki stamps (mig 0058). NULL on legacy rows and on rows
    # kept by the quality-keep path — the dirty predicate treats NULL as
    # "must regenerate".
    spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieval_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # P1 cited-only, retrieval-free fingerprint (mig 0064): a hash of just the
    # evidence the page actually CITED (node content_hash + summary, doc-chunk
    # text), recomputable from the DB by id — no embed call. Supersedes
    # `retrieval_fingerprint` (whole-bundle, embedder-dependent), which churned
    # on uncited top-k jitter and dirtied pages at zero real change. NULL →
    # "adopt" (compute + stamp on the next sync, NOT dirty), so a deploy or a
    # skipped backfill can never trigger a regeneration storm.
    cited_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wiki_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Edit-mode stamps (mig 0063). `content_src` is the raw pre-resolve body
    # carrying `[[node:qn]]` / `[[doc:path]]` placeholders; `content` is
    # post-resolve (placeholders already rendered to links), so the cheap edit
    # pass edits `content_src` and re-resolves. NULL on legacy / quality-keep
    # rows → edit-mode can't run → those pages take a full write until
    # rewritten once (safe: that is today's behaviour).
    content_src: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {code_node_id: content_hash} snapshot of every cited node at write time.
    # Ingest UPDATEs a changed node in place (same UUID, new content_hash), so
    # this catches a cited node's body change even when the node has dropped
    # out of the page's retrieval top-k. NULL → clause skipped (legacy).
    cited_content_hashes: Mapped[dict[str, str] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    # Consecutive cheap edits since the last full write; reset to 0 on a full
    # write. Caps slow prose drift — at `edit_streak_cap` the page is
    # force-rewritten from scratch.
    edit_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
