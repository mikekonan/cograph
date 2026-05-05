"""MdQueryService — read-only queries for markdown collections and documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.md_collection import MdCollection, MdDocument, MdChunk
from backend.app.models.user import User
from backend.app.models.enums import UserRole


@dataclass(slots=True, kw_only=True)
class MdCollectionListItem:
    id: UUID
    name: str
    description: str | None
    owner_id: UUID | None
    visibility: str
    document_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class MdCollectionListResult:
    items: list[MdCollectionListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(slots=True, kw_only=True)
class MdDocumentListItem:
    id: UUID
    source_key: str
    title: str | None
    bytes: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    content_updated_at: datetime | None


@dataclass(slots=True, kw_only=True)
class MdDocumentsPageResult:
    items: list[MdDocumentListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(slots=True, kw_only=True)
class MdCollectionDetail:
    id: UUID
    name: str
    description: str | None
    owner_id: UUID | None
    visibility: str
    documents: MdDocumentsPageResult
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class MdDocumentDetail:
    id: UUID
    collection_id: UUID
    source_key: str
    title: str | None
    content: str
    bytes: int
    word_count: int | None
    line_count: int | None
    frontmatter: dict[str, object]
    heading_tree: list[dict[str, object]]
    code_blocks: list[dict[str, object]]
    tables: list[dict[str, object]]
    links: list[dict[str, object]]
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class MdQueryService:
    async def list_collections(
        self,
        *,
        session: AsyncSession,
        current_user: User | None,
        page: int,
        per_page: int,
        search: str | None = None,
    ) -> MdCollectionListResult:
        query = select(MdCollection)
        if current_user is None or current_user.role is not UserRole.ADMIN:
            visibility_filter = MdCollection.visibility == "public"
            if current_user is not None:
                visibility_filter = visibility_filter | (MdCollection.owner_id == current_user.id)
            query = query.where(visibility_filter)
        if search:
            query = query.where(
                (MdCollection.name.ilike(f"%{search}%"))
                | (MdCollection.description.ilike(f"%{search}%"))
            )

        total = await session.scalar(
            select(func.count()).select_from(query.subquery())
        )
        offset = (page - 1) * per_page
        collections = list(
            (
                await session.scalars(
                    query.order_by(MdCollection.updated_at.desc())
                    .offset(offset)
                    .limit(per_page)
                )
            ).all()
        )
        collection_ids = [c.id for c in collections]
        doc_counts = await self._document_counts(
            session=session, collection_ids=collection_ids
        )

        return MdCollectionListResult(
            items=[
                MdCollectionListItem(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    owner_id=c.owner_id,
                    visibility=c.visibility.value,
                    document_count=doc_counts.get(c.id, 0),
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in collections
            ],
            total=total or 0,
            page=page,
            per_page=per_page,
            total_pages=ceil((total or 0) / per_page) if per_page > 0 else 0,
        )

    async def get_collection(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        page: int,
        per_page: int,
        search: str | None = None,
    ) -> MdCollectionDetail | None:
        collection = await session.get(MdCollection, collection_id)
        if collection is None:
            return None

        docs_query = select(MdDocument).where(
            MdDocument.collection_id == collection.id
        )
        if search:
            docs_query = docs_query.where(
                (MdDocument.source_key.ilike(f"%{search}%"))
                | (MdDocument.title.ilike(f"%{search}%"))
            )
        total = await session.scalar(
            select(func.count()).select_from(docs_query.subquery())
        )
        offset = (page - 1) * per_page
        documents = list(
            (
                await session.scalars(
                    docs_query.order_by(MdDocument.updated_at.desc())
                    .offset(offset)
                    .limit(per_page)
                )
            ).all()
        )
        document_ids = [d.id for d in documents]
        chunk_counts = await self._chunk_counts(
            session=session, document_ids=document_ids
        )

        return MdCollectionDetail(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            owner_id=collection.owner_id,
            visibility=collection.visibility.value,
            documents=MdDocumentsPageResult(
                items=[
                    MdDocumentListItem(
                        id=d.id,
                        source_key=d.source_key,
                        title=d.title,
                        bytes=d.bytes,
                        chunk_count=chunk_counts.get(d.id, 0),
                        created_at=d.created_at,
                        updated_at=d.updated_at,
                        content_updated_at=d.content_updated_at,
                    )
                    for d in documents
                ],
                total=total or 0,
                page=page,
                per_page=per_page,
                total_pages=ceil((total or 0) / per_page) if per_page > 0 else 0,
            ),
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    async def get_document(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        document_id: UUID,
    ) -> MdDocumentDetail | None:
        document = await session.scalar(
            select(MdDocument).where(
                MdDocument.collection_id == collection_id,
                MdDocument.id == document_id,
            )
        )
        if document is None:
            return None

        chunk_count = await session.scalar(
            select(func.count())
            .select_from(MdChunk)
            .where(MdChunk.document_id == document.id)
        ) or 0

        return MdDocumentDetail(
            id=document.id,
            collection_id=document.collection_id,
            source_key=document.source_key,
            title=document.title,
            content=document.content,
            bytes=document.bytes,
            word_count=document.word_count,
            line_count=document.line_count,
            frontmatter=document.frontmatter,
            heading_tree=document.heading_tree,
            code_blocks=document.code_blocks,
            tables=document.tables,
            links=document.links,
            chunk_count=chunk_count,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def _document_counts(
        self,
        *,
        session: AsyncSession,
        collection_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not collection_ids:
            return {}
        rows = await session.execute(
            select(MdDocument.collection_id, func.count())
            .where(MdDocument.collection_id.in_(tuple(collection_ids)))
            .group_by(MdDocument.collection_id)
        )
        return {cid: count for cid, count in rows.all()}

    async def _chunk_counts(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not document_ids:
            return {}
        rows = await session.execute(
            select(MdChunk.document_id, func.count())
            .where(MdChunk.document_id.in_(tuple(document_ids)))
            .group_by(MdChunk.document_id)
        )
        return {did: count for did, count in rows.all()}
