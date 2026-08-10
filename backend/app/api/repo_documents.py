from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import get_current_user_optional, get_db_session, get_settings_dep
from backend.app.core.errors import ApiError
from backend.app.core.repository_access import get_readable_repository_by_slug
from backend.app.models.enums import RepositoryStatus
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.repo_docs.queries import RepoDocumentQueryService

router = APIRouter(prefix="/repos", tags=["repo-documents"])

_repo_document_query_service = RepoDocumentQueryService()


class RepoDocumentListItemResponse(BaseModel):
    id: UUID
    repository_id: UUID
    file_path: str
    title: str | None
    bytes: int
    chunk_count: int
    mentions_count: int
    excerpt: str | None
    updated_at: datetime


class RepoDocumentListResponse(BaseModel):
    items: list[RepoDocumentListItemResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class RepoDocumentMentionResponse(BaseModel):
    node_id: UUID
    name: str
    file_path: str


class RepoDocumentChunkResponse(BaseModel):
    id: UUID
    chunk_index: int
    heading_path: list[str]
    mentions: list[RepoDocumentMentionResponse]


class RepoDocumentDetailResponse(BaseModel):
    id: UUID
    repository_id: UUID
    file_path: str
    title: str | None
    content: str
    bytes: int
    chunks: list[RepoDocumentChunkResponse]
    created_at: datetime
    updated_at: datetime


@router.get("/{host}/{owner}/{name}/documents", response_model=RepoDocumentListResponse)
async def list_repository_documents(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
) -> RepoDocumentListResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    result = await _repo_document_query_service.list_documents(
        session=session,
        repository_id=repository.id,
        page=page,
        per_page=per_page,
        search=search,
    )
    return RepoDocumentListResponse(
        items=[
            RepoDocumentListItemResponse(
                id=item.id,
                repository_id=item.repository_id,
                file_path=item.file_path,
                title=item.title,
                bytes=item.bytes,
                chunk_count=item.chunk_count,
                mentions_count=item.mentions_count,
                excerpt=item.excerpt,
                updated_at=item.updated_at,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
        total_pages=result.total_pages,
    )


@router.get("/{host}/{owner}/{name}/documents/{document_id}", response_model=RepoDocumentDetailResponse)
async def get_repository_document(
    host: str,
    owner: str,
    name: str,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> RepoDocumentDetailResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    document = await _repo_document_query_service.get_document(
        session=session,
        repository_id=repository.id,
        document_id=document_id,
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Repository document not found")

    return RepoDocumentDetailResponse(
        id=document.id,
        repository_id=document.repository_id,
        file_path=document.file_path,
        title=document.title,
        content=document.content,
        bytes=document.bytes,
        chunks=[
            RepoDocumentChunkResponse(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
                mentions=[
                    RepoDocumentMentionResponse(
                        node_id=mention.node_id,
                        name=mention.name,
                        file_path=mention.file_path,
                    )
                    for mention in chunk.mentions
                ],
            )
            for chunk in document.chunks
        ],
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


async def _require_ready_repository(
    *,
    session: AsyncSession,
    host: str,
    owner: str,
    name: str,
    settings: Settings,
    current_user: User | None,
) -> Repository:
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ApiError(
            409,
            "REPO_NOT_READY",
            "Repository graph is not ready yet",
        )
    return repository
