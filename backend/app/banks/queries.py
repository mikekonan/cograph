from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk
from backend.app.models.user import User
from backend.app.models.enums import UserRole


@dataclass(slots=True, kw_only=True)
class BankListItem:
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    document_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class BankListResult:
    items: list[BankListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(slots=True, kw_only=True)
class BankDocumentListItem:
    id: UUID
    title: str
    source_kind: str
    source_key: str
    external_id: str | None
    bytes: int
    chunk_count: int
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class BankDetail:
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    documents: BankListDocumentsResult
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class BankListDocumentsResult:
    items: list[BankDocumentListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(slots=True, kw_only=True)
class BankDocumentChunkDetail:
    chunk_index: int
    heading_path: list[str]


@dataclass(slots=True, kw_only=True)
class BankDocumentDetail:
    id: UUID
    bank_id: UUID
    title: str
    source_kind: str
    source_key: str
    external_id: str | None
    content: str
    bytes: int
    chunks: list[BankDocumentChunkDetail]
    created_at: datetime
    updated_at: datetime


class BankQueryService:
    async def list_banks(
        self,
        *,
        session: AsyncSession,
        current_user: User,
        page: int,
        per_page: int,
    ) -> BankListResult:
        query = select(Bank)
        if current_user.role not in (UserRole.OWNER, UserRole.ADMIN):
            query = query.where(Bank.owner_id == current_user.id)

        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        offset = (page - 1) * per_page
        banks = list(
            (
                await session.scalars(
                    query.order_by(Bank.updated_at.desc())
                    .offset(offset)
                    .limit(per_page)
                )
            ).all()
        )
        bank_ids = [bank.id for bank in banks]
        document_counts = await self._document_counts(
            session=session, bank_ids=bank_ids
        )

        return BankListResult(
            items=[
                BankListItem(
                    id=bank.id,
                    name=bank.name,
                    description=bank.description,
                    owner_id=bank.owner_id,
                    document_count=document_counts.get(bank.id, 0),
                    created_at=bank.created_at,
                    updated_at=bank.updated_at,
                )
                for bank in banks
            ],
            total=total or 0,
            page=page,
            per_page=per_page,
            total_pages=ceil((total or 0) / per_page) if per_page > 0 else 0,
        )

    async def get_bank(
        self,
        *,
        session: AsyncSession,
        bank_id: UUID,
        page: int,
        per_page: int,
    ) -> BankDetail | None:
        bank = await session.get(Bank, bank_id)
        if bank is None:
            return None

        documents_query = select(BankDocument).where(BankDocument.bank_id == bank.id)
        total = await session.scalar(
            select(func.count()).select_from(documents_query.subquery())
        )
        offset = (page - 1) * per_page
        documents = list(
            (
                await session.scalars(
                    documents_query.order_by(
                        BankDocument.updated_at.desc(), BankDocument.title.asc()
                    )
                    .offset(offset)
                    .limit(per_page)
                )
            ).all()
        )
        document_ids = [document.id for document in documents]
        chunk_counts = await self._chunk_counts(
            session=session, document_ids=document_ids
        )

        return BankDetail(
            id=bank.id,
            name=bank.name,
            description=bank.description,
            owner_id=bank.owner_id,
            documents=BankListDocumentsResult(
                items=[
                    BankDocumentListItem(
                        id=document.id,
                        title=document.title,
                        source_kind=document.source_kind.value,
                        source_key=document.source_key,
                        external_id=document.external_id,
                        bytes=document.bytes,
                        chunk_count=chunk_counts.get(document.id, 0),
                        updated_at=document.updated_at,
                    )
                    for document in documents
                ],
                total=total or 0,
                page=page,
                per_page=per_page,
                total_pages=ceil((total or 0) / per_page) if per_page > 0 else 0,
            ),
            created_at=bank.created_at,
            updated_at=bank.updated_at,
        )

    async def get_document(
        self,
        *,
        session: AsyncSession,
        bank_id: UUID,
        document_id: UUID,
    ) -> BankDocumentDetail | None:
        document = await session.scalar(
            select(BankDocument).where(
                BankDocument.bank_id == bank_id,
                BankDocument.id == document_id,
            )
        )
        if document is None:
            return None

        chunks = list(
            (
                await session.scalars(
                    select(BankDocumentChunk)
                    .where(BankDocumentChunk.document_id == document.id)
                    .order_by(BankDocumentChunk.chunk_index.asc())
                )
            ).all()
        )
        return BankDocumentDetail(
            id=document.id,
            bank_id=document.bank_id,
            title=document.title,
            source_kind=document.source_kind.value,
            source_key=document.source_key,
            external_id=document.external_id,
            content=document.content,
            bytes=document.bytes,
            chunks=[
                BankDocumentChunkDetail(
                    chunk_index=chunk.chunk_index,
                    heading_path=list(chunk.heading_path),
                )
                for chunk in chunks
            ],
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def _document_counts(
        self,
        *,
        session: AsyncSession,
        bank_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not bank_ids:
            return {}
        rows = await session.execute(
            select(BankDocument.bank_id, func.count())
            .where(BankDocument.bank_id.in_(tuple(bank_ids)))
            .group_by(BankDocument.bank_id)
        )
        return {bank_id: count for bank_id, count in rows.all()}

    async def _chunk_counts(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not document_ids:
            return {}
        rows = await session.execute(
            select(BankDocumentChunk.document_id, func.count())
            .where(BankDocumentChunk.document_id.in_(tuple(document_ids)))
            .group_by(BankDocumentChunk.document_id)
        )
        return {document_id: count for document_id, count in rows.all()}
