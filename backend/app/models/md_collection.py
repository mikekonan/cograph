from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin
from backend.app.db.vector_type import TsvectorType, VectorType
from backend.app.models.enums import MdCollectionVisibility, MdJobKind, MdJobStatus, MdLinkType


class MdCollection(TimestampMixin, Base):
    __tablename__ = "md_collections"
    __table_args__ = (
        UniqueConstraint("name", name="uq_md_collections_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    visibility: Mapped[MdCollectionVisibility] = mapped_column(
        Enum(
            MdCollectionVisibility,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=MdCollectionVisibility.PRIVATE,
    )

    documents = relationship(
        "MdDocument",
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="MdDocument.updated_at.desc()",
    )


class MdDocument(TimestampMixin, Base):
    __tablename__ = "md_documents"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "source_key",
            name="uq_md_documents_collection_source_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frontmatter: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    heading_tree: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    code_blocks: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    tables: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    links: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    content_updated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    collection = relationship("MdCollection", back_populates="documents")
    chunks = relationship(
        "MdChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="MdChunk.chunk_index",
    )
    outgoing_links = relationship(
        "MdLink",
        foreign_keys="MdLink.source_document_id",
        back_populates="source_document",
        cascade="all, delete-orphan",
        order_by="MdLink.created_at.asc()",
    )
    incoming_links = relationship(
        "MdLink",
        foreign_keys="MdLink.target_document_id",
        back_populates="target_document",
        order_by="MdLink.created_at.asc()",
    )


class MdChunk(Base):
    __tablename__ = "md_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_md_chunks_document_chunk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_anchor: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        VectorType(1536), nullable=True
    )
    model: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    # Generated by Postgres (GENERATED ALWAYS AS STORED); never written by the app.
    content_tsv: Mapped[str | None] = mapped_column(
        "content_tsv",
        TsvectorType,
        nullable=True,
        server_default=sa.FetchedValue(),
        info={"derived": True},
    )

    document = relationship("MdDocument", back_populates="chunks")


class MdLink(Base):
    __tablename__ = "md_links"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "href",
            name="uq_md_links_source_href",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    link_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    href: Mapped[str] = mapped_column(Text, nullable=False)
    link_type: Mapped[MdLinkType] = mapped_column(
        Enum(
            MdLinkType,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=MdLinkType.MARKDOWN,
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    source_document = relationship(
        "MdDocument",
        foreign_keys=[source_document_id],
        back_populates="outgoing_links",
    )
    target_document = relationship(
        "MdDocument",
        foreign_keys=[target_document_id],
        back_populates="incoming_links",
    )


class MdJob(Base):
    __tablename__ = "md_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("md_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[MdJobKind] = mapped_column(
        Enum(
            MdJobKind,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[MdJobStatus] = mapped_column(
        Enum(
            MdJobStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=MdJobStatus.QUEUED,
    )
    result_summary: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
