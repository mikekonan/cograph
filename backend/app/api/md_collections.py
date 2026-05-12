from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from backend.app.auth.actor import AuthenticatedActor
from backend.app.core.group_permissions import has_collection_permission
from backend.app.core.md_collection_access import get_readable_md_collection
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    require_actor_csrf,
    require_csrf,
    require_current_user,
)
from backend.app.core.errors import ApiError, FieldError
from backend.app.llm.md_chunk_embedder import MdChunkEmbedderService
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.md_rag.indexer import MdDocumentInput, MdIndexer
from backend.app.md_rag.queries import MdQueryService
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument, MdJob
from backend.app.models.enums import GrantLevel, MdCollectionVisibility, UserRole
from backend.app.models.user import User
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.runtime import build_hybrid_retriever

router = APIRouter(prefix="/md-collections", tags=["md-collections"])
logger = logging.getLogger(__name__)

_md_query_service = MdQueryService()
_md_indexer = MdIndexer()


async def get_md_chunk_embedder(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> MdChunkEmbedderService:
    """Create a fresh MdChunkEmbedderService per request."""
    settings = request.app.state.settings
    providers = await build_runtime_providers(
        session=session,
        settings=settings,
    )
    return MdChunkEmbedderService(
        providers.embed_provider,
        batch_size=settings.embedding.batch_size,
    )


async def get_md_search_embed_provider(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Embed provider for md-collection search."""
    providers = await build_runtime_providers(
        session=session,
        settings=request.app.state.settings,
    )
    return providers.embed_provider


def get_md_hybrid_retriever(request: Request) -> HybridRetriever:
    return build_hybrid_retriever(request.app.state.settings)


async def _enqueue_md_rag_jobs(
    request: Request,
    session: AsyncSession,
    collection_id: UUID,
    changed_document_ids: list[UUID] | None = None,
) -> None:
    """Enqueue background embed + link resolve for a collection.

    Skips enqueuing when ``changed_document_ids`` is empty — unchanged
    documents don't need re-embedding or re-resolution.
    """
    if changed_document_ids is not None and not changed_document_ids:
        return

    from arq import create_pool

    from backend.app.md_rag.job_tracker import MdJobTracker
    from backend.app.models.enums import MdJobKind
    from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
    from backend.app.pipeline.worker import build_redis_settings

    settings = request.app.state.settings
    try:
        embed_job = await MdJobTracker.create(
            session, collection_id=collection_id, kind=MdJobKind.EMBED
        )
        link_job = await MdJobTracker.create(
            session, collection_id=collection_id, kind=MdJobKind.RESOLVE_LINKS
        )
        pool = await create_pool(
            build_redis_settings(settings.redis.url),
            default_queue_name=REPO_SYNC_QUEUE_NAME,
        )
        await pool.enqueue_job(
            "embed_md_collection",
            str(collection_id),
            str(embed_job.id),
        )
        await pool.enqueue_job(
            "resolve_md_links",
            str(collection_id),
            str(link_job.id),
        )
        await pool.aclose()
    except Exception as exc:
        logger.warning(
            "Failed to enqueue md-rag jobs",
            extra={"collection_id": str(collection_id), "error": str(exc)},
        )


_MAX_MD_DOCUMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_UPLOAD_SUFFIXES = {".md", ".mdx", ".txt"}
_TEXTUAL_UPLOAD_CONTENT_TYPES = {
    "text/markdown",
    "text/plain",
    "text/x-markdown",
}


class MdCollectionListItemResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    owner_id: UUID | None
    visibility: str
    document_count: int
    created_at: datetime
    updated_at: datetime


class MdCollectionListResponse(BaseModel):
    items: list[MdCollectionListItemResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class CreateMdCollectionRequest(BaseModel):
    name: str
    description: str | None = None
    visibility: MdCollectionVisibility = MdCollectionVisibility.PRIVATE


class UpdateMdCollectionRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: MdCollectionVisibility | None = None


class MdDocumentListItemResponse(BaseModel):
    id: UUID
    source_key: str
    title: str | None
    bytes: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    content_updated_at: datetime | None


class MdDocumentsPageResponse(BaseModel):
    items: list[MdDocumentListItemResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class MdCollectionDetailResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    owner_id: UUID | None
    visibility: str
    documents: MdDocumentsPageResponse
    created_at: datetime
    updated_at: datetime


class MdDocumentUploadResponse(BaseModel):
    id: UUID
    collection_id: UUID
    source_key: str
    title: str | None
    bytes: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class MdDocumentBatchUploadRequest(BaseModel):
    documents: list[MdDocumentBatchItem]
    upload_job_id: UUID | None = None
    upload_total: int | None = Field(default=None, ge=0)
    upload_final: bool = False


class MdDocumentBatchItem(BaseModel):
    source_key: str
    title: str | None = None
    content: str


class MdDocumentBatchUploadResponse(BaseModel):
    items: list[MdDocumentUploadResponse]
    indexed_documents: int
    indexed_chunks: int
    unchanged_documents: int
    upload_job_id: UUID | None = None


class MdDocumentDetailResponse(BaseModel):
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


@router.get("", response_model=MdCollectionListResponse)
async def list_md_collections(
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
) -> MdCollectionListResponse:
    result = await _md_query_service.list_collections(
        session=session,
        current_user=current_user,
        page=page,
        per_page=per_page,
        search=search,
    )
    return MdCollectionListResponse(
        items=[
            MdCollectionListItemResponse(
                id=item.id,
                name=item.name,
                description=item.description,
                owner_id=item.owner_id,
                visibility=item.visibility,
                document_count=item.document_count,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
        total_pages=result.total_pages,
    )


@router.post(
    "", response_model=MdCollectionDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_md_collection(
    payload: CreateMdCollectionRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> MdCollectionDetailResponse:
    del _csrf
    name = _require_collection_name(payload.name)

    from sqlalchemy import select

    existing = await session.scalar(
        select(MdCollection).where(MdCollection.name == name)
    )
    if existing is not None:
        raise ApiError(
            409, "DUPLICATE_NAME", f"A collection named '{name}' already exists."
        )

    collection = MdCollection(
        name=name,
        description=payload.description,
        owner_id=current_user.id,
        visibility=payload.visibility,
    )
    session.add(collection)
    await session.commit()
    detail = await _md_query_service.get_collection(
        session=session,
        collection_id=collection.id,
        page=1,
        per_page=20,
    )
    assert detail is not None
    return _build_collection_detail_response(detail)


@router.get("/{collection_id}", response_model=MdCollectionDetailResponse)
async def get_md_collection(
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
) -> MdCollectionDetailResponse:
    await _require_collection_access(
        session=session, collection_id=collection_id, current_user=current_user
    )
    detail = await _md_query_service.get_collection(
        session=session,
        collection_id=collection_id,
        page=page,
        per_page=per_page,
        search=search,
    )
    if detail is None:
        raise ApiError(404, "NOT_FOUND", "Collection not found")
    return _build_collection_detail_response(detail)


@router.patch("/{collection_id}", response_model=MdCollectionDetailResponse)
async def update_md_collection(
    collection_id: UUID,
    payload: UpdateMdCollectionRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> MdCollectionDetailResponse:
    del _csrf
    collection = await _require_collection_owner_or_admin(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
    )
    if payload.name is not None:
        new_name = _require_collection_name(payload.name)
        if new_name != collection.name:
            from sqlalchemy import select

            existing = await session.scalar(
                select(MdCollection).where(
                    MdCollection.name == new_name,
                    MdCollection.id != collection.id,
                )
            )
            if existing is not None:
                raise ApiError(
                    409,
                    "DUPLICATE_NAME",
                    f"A collection named '{new_name}' already exists.",
                )
        collection.name = new_name
    if payload.description is not None:
        collection.description = payload.description
    if payload.visibility is not None:
        collection.visibility = payload.visibility
    await session.commit()
    # `TimestampMixin.updated_at` has `onupdate=now()`, so the in-session
    # row's `updated_at` is now stale relative to the DB. The detail
    # builder below reads `collection.updated_at` — without an explicit
    # async refresh, the read triggers a lazy DB hit in sync context
    # and trips SQLAlchemy's `MissingGreenlet` guard. Refresh explicitly
    # so the read stays in-memory.
    await session.refresh(collection)

    detail = await _md_query_service.get_collection(
        session=session,
        collection_id=collection.id,
        page=1,
        per_page=20,
    )
    assert detail is not None
    return _build_collection_detail_response(detail)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_md_collection(
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf
    collection = await _require_collection_for_mutation(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
        required=GrantLevel.ADMIN,
    )
    await session.delete(collection)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{collection_id}/documents",
    response_model=MdDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_md_document(
    request: Request,
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> MdDocumentUploadResponse:
    await _require_collection_owner_or_admin(
        session=session,
        collection_id=collection_id,
        current_user=actor.user,
    )
    document_input = await _parse_single_document_upload(request)
    result = await _md_indexer.upsert_document(
        session=session,
        collection_id=collection_id,
        document=document_input,
    )
    await session.commit()
    changed_ids = [result.id] if not result.unchanged else []
    await _enqueue_md_rag_jobs(
        request=request,
        session=session,
        collection_id=collection_id,
        changed_document_ids=changed_ids,
    )
    return _build_document_upload_response(result)


@router.post(
    "/{collection_id}/documents/batch",
    response_model=MdDocumentBatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_md_document_batch(
    request: Request,
    collection_id: UUID,
    payload: MdDocumentBatchUploadRequest,
    session: AsyncSession = Depends(get_db_session),
    actor: AuthenticatedActor = Depends(require_actor_csrf),
) -> MdDocumentBatchUploadResponse:
    await _require_collection_owner_or_admin(
        session=session,
        collection_id=collection_id,
        current_user=actor.user,
    )
    documents = [
        MdDocumentInput(
            source_key=_require_source_key(item.source_key),
            title=item.title,
            content=item.content,
        )
        for item in payload.documents
    ]
    _validate_batch_source_keys(documents)

    upload_job = await _attach_or_create_upload_job(
        session=session,
        collection_id=collection_id,
        upload_job_id=payload.upload_job_id,
        upload_total=payload.upload_total,
    )

    result = await _md_indexer.upsert_documents(
        session=session,
        collection_id=collection_id,
        documents=documents,
    )
    await session.commit()
    changed_document_ids = [item.id for item in result.items if not item.unchanged]
    await _enqueue_md_rag_jobs(
        request=request,
        session=session,
        collection_id=collection_id,
        changed_document_ids=changed_document_ids,
    )

    if upload_job is not None:
        await _advance_upload_job(
            session=session,
            job=upload_job,
            batch_size=len(documents),
            final=payload.upload_final,
        )

    return MdDocumentBatchUploadResponse(
        items=[_build_document_upload_response(item) for item in result.items],
        indexed_documents=result.indexed_documents,
        indexed_chunks=result.indexed_chunks,
        unchanged_documents=result.unchanged_documents,
        upload_job_id=upload_job.id if upload_job is not None else None,
    )


@router.get(
    "/{collection_id}/documents/{document_id}",
    response_model=MdDocumentDetailResponse,
)
async def get_md_document(
    collection_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
) -> MdDocumentDetailResponse:
    await _require_collection_access(
        session=session, collection_id=collection_id, current_user=current_user
    )
    document = await _md_query_service.get_document(
        session=session,
        collection_id=collection_id,
        document_id=document_id,
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Document not found")
    return MdDocumentDetailResponse(
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
        chunk_count=document.chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.delete(
    "/{collection_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_md_document(
    collection_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf
    await _require_collection_owner_or_admin(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
    )
    document = await session.scalar(
        select(MdDocument).where(
            MdDocument.collection_id == collection_id,
            MdDocument.id == document_id,
        )
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Document not found")
    await session.delete(document)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MdChunkResponse(BaseModel):
    id: UUID
    chunk_index: int
    heading_path: list[str]
    heading_level: int | None
    section_anchor: str | None
    content: str


class MdChunkListResponse(BaseModel):
    items: list[MdChunkResponse]


@router.get(
    "/{collection_id}/documents/{document_id}/chunks",
    response_model=MdChunkListResponse,
)
async def list_md_document_chunks(
    collection_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
) -> MdChunkListResponse:
    await _require_collection_access(
        session=session, collection_id=collection_id, current_user=current_user
    )
    document = await session.scalar(
        select(MdDocument).where(
            MdDocument.collection_id == collection_id,
            MdDocument.id == document_id,
        )
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Document not found")

    chunks = list(
        (
            await session.scalars(
                select(MdChunk)
                .where(MdChunk.document_id == document_id)
                .order_by(MdChunk.chunk_index)
            )
        ).all()
    )

    return MdChunkListResponse(
        items=[
            MdChunkResponse(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
                heading_level=chunk.heading_level,
                section_anchor=chunk.section_anchor,
                content=chunk.content,
            )
            for chunk in chunks
        ]
    )


class MdCollectionSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)


class MdSearchResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    source_key: str
    title: str | None
    heading_path: list[str]
    content: str
    score: float
    vector_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None


class MdCollectionSearchResponse(BaseModel):
    results: list[MdSearchResult]


class MdEmbedStatusResponse(BaseModel):
    total_chunks: int
    embedded_chunks: int
    is_ready: bool


@router.get("/{collection_id}/embed-status", response_model=MdEmbedStatusResponse)
async def get_md_collection_embed_status(
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
) -> MdEmbedStatusResponse:
    await _require_collection_access(
        session=session, collection_id=collection_id, current_user=current_user
    )
    total = (
        await session.scalar(
            select(func.count(MdChunk.id))
            .join(MdDocument)
            .where(MdDocument.collection_id == collection_id)
        )
        or 0
    )
    embedded = (
        await session.scalar(
            select(func.count(MdChunk.id))
            .join(MdDocument)
            .where(
                MdDocument.collection_id == collection_id,
                MdChunk.embedding.is_not(None),
            )
        )
        or 0
    )
    return MdEmbedStatusResponse(
        total_chunks=total,
        embedded_chunks=embedded,
        is_ready=total > 0 and embedded == total,
    )


@router.post("/{collection_id}/search", response_model=MdCollectionSearchResponse)
async def search_md_collection(
    collection_id: UUID,
    payload: MdCollectionSearchRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
    embed_provider=Depends(get_md_search_embed_provider),
    retriever: HybridRetriever = Depends(get_md_hybrid_retriever),
) -> MdCollectionSearchResponse:
    await _require_collection_access(
        session=session, collection_id=collection_id, current_user=current_user
    )

    if embed_provider is None:
        raise ApiError(
            503,
            "RETRIEVAL_UNAVAILABLE",
            "Search requires an embedding provider to be configured",
        )

    try:
        query_embedding = (await embed_provider.embed([payload.query]))[0]
    except Exception as exc:
        raise ApiError(
            503,
            "EMBEDDING_PROVIDER_FAILED",
            "Embedding provider unavailable",
        ) from exc

    chunks = await retriever.retrieve(
        session,
        query_text=payload.query,
        query_embedding=query_embedding,
        collection_id=collection_id,
        top_k=payload.top_k,
        stores={"md_collections"},
    )

    return MdCollectionSearchResponse(
        results=[
            MdSearchResult(
                chunk_id=chunk.chunk_id,
                document_id=chunk.metadata.get("document_id"),
                source_key=chunk.metadata.get("source_key", ""),
                title=chunk.metadata.get("title"),
                heading_path=chunk.metadata.get("heading_path", []),
                content=chunk.content,
                score=chunk.score,
                vector_rank=chunk.metadata.get("vector_rank"),
                lexical_rank=chunk.metadata.get("lexical_rank"),
                rerank_score=chunk.metadata.get("rerank_score"),
            )
            for chunk in chunks
        ]
    )


class MdJobResponse(BaseModel):
    id: UUID
    collection_id: UUID
    kind: str
    status: str
    result_summary: dict[str, object]
    error_message: str | None
    current_item: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MdJobListResponse(BaseModel):
    items: list[MdJobResponse]


class MdJobWithCollectionResponse(BaseModel):
    id: UUID
    collection_id: UUID
    collection_name: str
    kind: str
    status: str
    result_summary: dict[str, object]
    error_message: str | None
    current_item: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MdGlobalJobListResponse(BaseModel):
    items: list[MdJobWithCollectionResponse]


@router.get(
    "/-/jobs",
    response_model=MdGlobalJobListResponse,
)
async def list_all_md_jobs(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    limit: int = Query(100, ge=1, le=500),
    status: str | None = Query(None),
) -> MdGlobalJobListResponse:
    from backend.app.md_rag.job_tracker import MdJobTracker
    from backend.app.models.enums import MdJobStatus

    status_filter: MdJobStatus | None = None
    if status:
        try:
            status_filter = MdJobStatus(status)
        except ValueError:
            pass

    rows = await MdJobTracker.list_all_visible(
        session, current_user, limit=limit, status=status_filter
    )
    return MdGlobalJobListResponse(
        items=[
            MdJobWithCollectionResponse(
                id=job.id,
                collection_id=job.collection_id,
                collection_name=name,
                kind=job.kind.value,
                status=job.status.value,
                result_summary=job.result_summary,
                error_message=job.error_message,
                current_item=job.result_summary.get("current_item")
                if job.result_summary
                else None,
                created_at=job.created_at,
                updated_at=job.updated_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
            for job, name in rows
        ]
    )


@router.get(
    "/{collection_id}/jobs",
    response_model=MdJobListResponse,
)
async def list_md_collection_jobs(
    collection_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User | None = Depends(get_current_user_optional),
    limit: int = Query(20, ge=1, le=100),
) -> MdJobListResponse:
    collection = await _require_collection_access(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
    )
    from backend.app.md_rag.job_tracker import MdJobTracker

    jobs = await MdJobTracker.list_for_collection(
        session, collection_id=collection.id, limit=limit
    )
    return MdJobListResponse(
        items=[
            MdJobResponse(
                id=job.id,
                collection_id=job.collection_id,
                kind=job.kind.value,
                status=job.status.value,
                result_summary=job.result_summary,
                error_message=job.error_message,
                current_item=job.result_summary.get("current_item")
                if job.result_summary
                else None,
                created_at=job.created_at,
                updated_at=job.updated_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
            for job in jobs
        ]
    )


@router.post("/-/jobs/{job_id}/retry", response_model=MdJobResponse)
async def retry_md_job(
    job_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> MdJobResponse:
    del _csrf
    from backend.app.md_rag.job_tracker import MdJobTracker
    from backend.app.models.enums import MdJobKind

    # Find the original job to check access
    original = await session.get(MdJob, job_id)
    if original is None:
        raise ApiError(404, "NOT_FOUND", "Job not found")

    # Retrying is a write/costly background operation, so visibility is not enough.
    await _require_collection_owner_or_admin(
        session=session,
        collection_id=original.collection_id,
        current_user=current_user,
    )

    new_job = await MdJobTracker.retry(session, job_id=job_id)
    if new_job is None:
        raise ApiError(404, "NOT_FOUND", "Job not found")

    # Enqueue the new job
    settings = request.app.state.settings
    try:
        from arq import create_pool
        from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
        from backend.app.pipeline.worker import build_redis_settings

        pool = await create_pool(
            build_redis_settings(settings.redis.url),
            default_queue_name=REPO_SYNC_QUEUE_NAME,
        )
        if new_job.kind == MdJobKind.EMBED:
            await pool.enqueue_job(
                "embed_md_collection",
                str(new_job.collection_id),
                str(new_job.id),
            )
        elif new_job.kind == MdJobKind.RESOLVE_LINKS:
            await pool.enqueue_job(
                "resolve_md_links",
                str(new_job.collection_id),
                str(new_job.id),
            )
        await pool.aclose()
    except Exception as exc:
        logger.warning(
            "Failed to enqueue retried md-rag job",
            extra={"job_id": str(new_job.id), "error": str(exc)},
        )

    return MdJobResponse(
        id=new_job.id,
        collection_id=new_job.collection_id,
        kind=new_job.kind.value,
        status=new_job.status.value,
        result_summary=new_job.result_summary,
        error_message=new_job.error_message,
        current_item=None,
        created_at=new_job.created_at,
        updated_at=new_job.updated_at,
        started_at=new_job.started_at,
        finished_at=new_job.finished_at,
    )


@router.post("/{collection_id}/re-embed", response_model=MdJobResponse)
async def reembed_md_collection(
    collection_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> MdJobResponse:
    del _csrf
    from backend.app.md_rag.job_tracker import MdJobTracker
    from backend.app.models.enums import MdJobKind

    await _require_collection_owner_or_admin(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
    )

    embed_job = await MdJobTracker.create(
        session, collection_id=collection_id, kind=MdJobKind.EMBED
    )

    settings = request.app.state.settings
    try:
        from arq import create_pool
        from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
        from backend.app.pipeline.worker import build_redis_settings

        pool = await create_pool(
            build_redis_settings(settings.redis.url),
            default_queue_name=REPO_SYNC_QUEUE_NAME,
        )
        await pool.enqueue_job(
            "embed_md_collection",
            str(collection_id),
            str(embed_job.id),
        )
        await pool.aclose()
    except Exception as exc:
        logger.warning(
            "Failed to enqueue re-embed md-rag job",
            extra={"collection_id": str(collection_id), "error": str(exc)},
        )

    return MdJobResponse(
        id=embed_job.id,
        collection_id=embed_job.collection_id,
        kind=embed_job.kind.value,
        status=embed_job.status.value,
        result_summary=embed_job.result_summary,
        error_message=embed_job.error_message,
        current_item=None,
        created_at=embed_job.created_at,
        updated_at=embed_job.updated_at,
        started_at=embed_job.started_at,
        finished_at=embed_job.finished_at,
    )


def _build_collection_detail_response(detail) -> MdCollectionDetailResponse:
    return MdCollectionDetailResponse(
        id=detail.id,
        name=detail.name,
        description=detail.description,
        owner_id=detail.owner_id,
        visibility=detail.visibility,
        documents=MdDocumentsPageResponse(
            items=[
                MdDocumentListItemResponse(
                    id=item.id,
                    source_key=item.source_key,
                    title=item.title,
                    bytes=item.bytes,
                    chunk_count=item.chunk_count,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    content_updated_at=item.content_updated_at,
                )
                for item in detail.documents.items
            ],
            total=detail.documents.total,
            page=detail.documents.page,
            per_page=detail.documents.per_page,
            total_pages=detail.documents.total_pages,
        ),
        created_at=detail.created_at,
        updated_at=detail.updated_at,
    )


def _build_document_upload_response(result) -> MdDocumentUploadResponse:
    return MdDocumentUploadResponse(
        id=result.id,
        collection_id=result.collection_id,
        source_key=result.source_key,
        title=result.title,
        bytes=result.bytes,
        chunk_count=result.chunk_count,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


async def _parse_single_document_upload(
    request: Request,
) -> MdDocumentInput:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = MdDocumentBatchItem.model_validate(await request.json())
        return MdDocumentInput(
            source_key=_require_source_key(payload.source_key),
            title=payload.title,
            content=payload.content,
        )
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Request validation failed",
                field_errors=[
                    FieldError(
                        field="file",
                        code="REQUIRED",
                        message="file is required",
                    )
                ],
            )
        source_key = str(form.get("source_key") or upload.filename or "").strip()
        title = str(form.get("title") or "").strip() or None
        content = await _read_upload_content(upload)
        return MdDocumentInput(
            source_key=_require_source_key(source_key),
            title=title,
            content=content,
        )
    raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Unsupported content type")


async def _read_upload_content(upload: UploadFile) -> str:
    filename = upload.filename or ""
    suffix = ""
    if "." in filename:
        suffix = f".{filename.rsplit('.', 1)[1].lower()}"
    if not _is_supported_upload(
        suffix=suffix,
        content_type=upload.content_type,
    ):
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Unsupported document type")

    content_bytes = await upload.read()
    if len(content_bytes) > _MAX_MD_DOCUMENT_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Document exceeds 10 MB limit")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(
            415, "UNSUPPORTED_MEDIA_TYPE", "Document must be UTF-8 text"
        ) from exc
    return content


def _is_supported_upload(*, suffix: str, content_type: str | None) -> bool:
    if suffix in _ALLOWED_UPLOAD_SUFFIXES:
        return True
    if not suffix and content_type in _TEXTUAL_UPLOAD_CONTENT_TYPES:
        return True
    return False


def _require_source_key(source_key: str) -> str:
    normalized = source_key.strip()
    if normalized:
        return normalized
    raise ApiError(
        422,
        "VALIDATION_FAILED",
        "Request validation failed",
        field_errors=[
            FieldError(
                field="source_key",
                code="REQUIRED",
                message="source_key is required",
            )
        ],
    )


def _require_collection_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Request validation failed",
            field_errors=[
                FieldError(
                    field="name",
                    code="REQUIRED",
                    message="name is required",
                )
            ],
        )
    if len(normalized) > 255:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Request validation failed",
            field_errors=[
                FieldError(
                    field="name",
                    code="INVALID",
                    message="name must be at most 255 characters",
                )
            ],
        )
    return normalized


def _validate_batch_source_keys(documents: list[MdDocumentInput]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for document in documents:
        if document.source_key in seen:
            duplicates.add(document.source_key)
        seen.add(document.source_key)
    if not duplicates:
        return
    duplicate_list = ", ".join(sorted(duplicates))
    raise ApiError(
        422,
        "VALIDATION_FAILED",
        f"Duplicate source_key values in batch: {duplicate_list}",
        field_errors=[
            FieldError(
                field="documents",
                code="INVALID",
                message=f"Duplicate source_key values in batch: {duplicate_list}",
            )
        ],
    )


async def _require_collection_access(
    *,
    session: AsyncSession,
    collection_id: UUID,
    current_user: User | None,
) -> MdCollection:
    return await get_readable_md_collection(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
    )


async def _require_collection_for_mutation(
    *,
    session: AsyncSession,
    collection_id: UUID,
    current_user: User,
    required: GrantLevel,
) -> MdCollection:
    """Resolve a collection by id AND assert the caller has `required` on it.

    Ladder semantics:

    * OWNER/ADMIN role short-circuits.
    * Collection's `owner_id == current_user.id` short-circuits — the
      pre-existing self-access semantic is preserved through this
      rewrite so users who created their own private collection keep
      full WRITE/ADMIN power without needing a synthetic group.
    * Otherwise: a group grant on this collection must satisfy
      `level >= required` per the (READ=1, WRITE=2, ADMIN=3) ladder.

    Returns the loaded MdCollection on success; raises 404 if it
    doesn't exist (or has no row), 403 if the caller can't pass the
    ladder check.
    """
    collection = await session.get(MdCollection, collection_id)
    if collection is None:
        raise ApiError(404, "NOT_FOUND", "Collection not found")
    if current_user.role in (UserRole.OWNER, UserRole.ADMIN):
        return collection
    if collection.owner_id == current_user.id:
        return collection
    if await has_collection_permission(
        session, current_user, collection, required
    ):
        return collection
    raise ApiError(403, "FORBIDDEN", "Collection access denied")


# Back-compat shim: existing call sites that used the old
# "owner-or-admin" gate now flow through the parametrized helper at
# WRITE level. ADMIN-only sites pass `required=GrantLevel.ADMIN`
# directly; we keep the old name as a deprecated alias so anything
# importing it from elsewhere still resolves until the next refactor.
async def _require_collection_owner_or_admin(
    *,
    session: AsyncSession,
    collection_id: UUID,
    current_user: User,
) -> MdCollection:
    return await _require_collection_for_mutation(
        session=session,
        collection_id=collection_id,
        current_user=current_user,
        required=GrantLevel.WRITE,
    )


async def _attach_or_create_upload_job(
    *,
    session: AsyncSession,
    collection_id: UUID,
    upload_job_id: UUID | None,
    upload_total: int | None,
) -> MdJob | None:
    """Resolve the MdJob row tracking a bulk-upload session.

    The batch route is also reachable from PAT scripts that don't care
    about progress UI; in that case both ``upload_job_id`` and
    ``upload_total`` are ``None`` and we return ``None`` (no row
    created — the embed/resolve_links jobs that follow are sufficient).

    When the FE bulk-uploader sends the first batch it provides
    ``upload_total``; we create a fresh ``kind=upload`` row and the
    handler returns the new id. Subsequent batches echo
    ``upload_job_id`` back; we attach to the existing row, validate it
    matches this collection and is still active, and the handler
    advances ``processed`` after the indexer succeeds.
    """
    from backend.app.md_rag.job_tracker import MdJobTracker
    from backend.app.models.enums import MdJobKind, MdJobStatus

    if upload_job_id is None and upload_total is None:
        return None

    if upload_job_id is not None:
        job = await session.get(MdJob, upload_job_id)
        if job is None:
            raise ApiError(404, "NOT_FOUND", "Upload job not found")
        if job.collection_id != collection_id:
            raise ApiError(403, "FORBIDDEN", "Upload job belongs to another collection")
        if job.kind is not MdJobKind.UPLOAD:
            raise ApiError(409, "INVALID_JOB_KIND", "Job is not an upload tracker")
        if job.status not in (MdJobStatus.QUEUED, MdJobStatus.RUNNING):
            raise ApiError(409, "JOB_TERMINAL", "Upload job has already finished")
        return job

    job = await MdJobTracker.create(
        session, collection_id=collection_id, kind=MdJobKind.UPLOAD
    )
    job.result_summary = {
        "total": upload_total or 0,
        "processed": 0,
        "failed": 0,
        "current_item": None,
    }
    await session.commit()
    await session.refresh(job)
    return job


async def _advance_upload_job(
    *,
    session: AsyncSession,
    job: MdJob,
    batch_size: int,
    final: bool,
) -> None:
    """Advance a ``kind=upload`` MdJob after a successful batch upsert."""
    from datetime import UTC, datetime

    from backend.app.models.enums import MdJobStatus

    summary = dict(job.result_summary or {})
    processed = int(summary.get("processed", 0)) + batch_size
    total = int(summary.get("total", 0))
    summary["processed"] = processed

    job.result_summary = summary
    if job.status is MdJobStatus.QUEUED:
        job.status = MdJobStatus.RUNNING
        job.started_at = datetime.now(UTC)

    if final or (total > 0 and processed >= total):
        job.status = MdJobStatus.SUCCESS
        job.finished_at = datetime.now(UTC)

    await session.commit()
