from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from backend.app.banks.indexer import BankDocumentUpsertInput, BankIndexer
from backend.app.banks.queries import BankQueryService
from backend.app.core.bank_access import get_readable_bank
from backend.app.core.deps import get_db_session, require_csrf, require_current_user
from backend.app.core.errors import ApiError, FieldError
from backend.app.llm.bank_fact_extractor import BankFactExtractorService
from backend.app.llm.bank_document_embedder import BankDocumentEmbedderService
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.bank import Bank, BankDocument
from backend.app.models.user import User

router = APIRouter(prefix="/banks", tags=["banks"])

_bank_query_service = BankQueryService()
_bank_indexer = BankIndexer()


async def get_bank_document_embedder(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> BankDocumentEmbedderService:
    """Create a fresh BankDocumentEmbedderService per request."""
    settings = request.app.state.settings
    providers = await build_runtime_providers(
        session=session,
        settings=settings,
    )
    return BankDocumentEmbedderService(
        providers.embed_provider,
        batch_size=settings.embedding.batch_size,
    )


async def get_bank_fact_extractor(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> BankFactExtractorService | None:
    settings = request.app.state.settings
    providers = await build_runtime_providers(
        session=session,
        settings=settings,
    )
    if providers.completion_provider is None:
        return None
    return BankFactExtractorService(
        llm=providers.completion_provider,
        embed_provider=providers.embed_provider,
    )


_MAX_BANK_NAME_LENGTH = 255
_MAX_BANK_DOCUMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_UPLOAD_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_TEXTUAL_UPLOAD_CONTENT_TYPES = {
    "text/markdown",
    "text/plain",
    "text/x-markdown",
}


class BankListItemResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    document_count: int
    created_at: datetime
    updated_at: datetime


class BankListResponse(BaseModel):
    items: list[BankListItemResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class CreateBankRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateBankRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class BankDocumentListItemResponse(BaseModel):
    id: UUID
    title: str
    source_kind: str
    source_key: str
    external_id: str | None
    bytes: int
    chunk_count: int
    updated_at: datetime


class BankDocumentsPageResponse(BaseModel):
    items: list[BankDocumentListItemResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class BankDetailResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    documents: BankDocumentsPageResponse
    created_at: datetime
    updated_at: datetime


class BankDocumentUploadRequest(BaseModel):
    source_key: str
    title: str | None = None
    content: str


class BankDocumentBatchUploadRequest(BaseModel):
    documents: list[BankDocumentUploadRequest]


class BankDocumentUploadResponse(BaseModel):
    id: UUID
    bank_id: UUID
    title: str
    source_kind: str
    source_key: str
    external_id: str | None
    bytes: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class BankDocumentBatchUploadResponse(BaseModel):
    items: list[BankDocumentUploadResponse]
    indexed_documents: int
    indexed_chunks: int
    unchanged_documents: int


class BankDocumentChunkResponse(BaseModel):
    chunk_index: int
    heading_path: list[str]


class BankDocumentDetailResponse(BaseModel):
    id: UUID
    bank_id: UUID
    title: str
    source_kind: str
    source_key: str
    external_id: str | None
    content: str
    bytes: int
    chunks: list[BankDocumentChunkResponse]
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=BankListResponse)
async def list_banks(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> BankListResponse:
    result = await _bank_query_service.list_banks(
        session=session,
        current_user=current_user,
        page=page,
        per_page=per_page,
    )
    return BankListResponse(
        items=[
            BankListItemResponse(
                id=item.id,
                name=item.name,
                description=item.description,
                owner_id=item.owner_id,
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


@router.post("", response_model=BankDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_bank(
    payload: CreateBankRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> BankDetailResponse:
    del _csrf
    name = _require_bank_name(payload.name)
    bank = Bank(
        name=name,
        description=payload.description,
        owner_id=current_user.id,
    )
    session.add(bank)
    await session.commit()
    detail = await _bank_query_service.get_bank(
        session=session,
        bank_id=bank.id,
        page=1,
        per_page=20,
    )
    assert detail is not None
    return _build_bank_detail_response(detail)


@router.get("/{bank_id}", response_model=BankDetailResponse)
async def get_bank(
    bank_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> BankDetailResponse:
    await _require_bank_access(
        session=session, bank_id=bank_id, current_user=current_user
    )
    detail = await _bank_query_service.get_bank(
        session=session,
        bank_id=bank_id,
        page=page,
        per_page=per_page,
    )
    if detail is None:
        raise ApiError(404, "NOT_FOUND", "Bank not found")
    return _build_bank_detail_response(detail)


@router.patch("/{bank_id}", response_model=BankDetailResponse)
async def update_bank(
    bank_id: UUID,
    payload: UpdateBankRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> BankDetailResponse:
    del _csrf
    bank = await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    if payload.name is not None:
        bank.name = _require_bank_name(payload.name)
    if payload.description is not None:
        bank.description = payload.description
    await session.commit()

    detail = await _bank_query_service.get_bank(
        session=session,
        bank_id=bank.id,
        page=1,
        per_page=20,
    )
    assert detail is not None
    return _build_bank_detail_response(detail)


@router.delete("/{bank_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bank(
    bank_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf
    bank = await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    await session.delete(bank)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{bank_id}/documents",
    response_model=BankDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_bank_document(
    request: Request,
    bank_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
    embedder: BankDocumentEmbedderService | None = Depends(get_bank_document_embedder),
    fact_extractor: BankFactExtractorService | None = Depends(get_bank_fact_extractor),
) -> BankDocumentUploadResponse:
    del _csrf
    await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    document_input = await _parse_single_document_upload(request)
    result = await _bank_indexer.upsert_document(
        session=session,
        bank_id=bank_id,
        document=document_input,
    )
    if embedder is not None:
        await embedder.embed_documents(session=session, document_ids=[result.id])
    if fact_extractor is not None and not result.unchanged:
        await fact_extractor.extract_documents(
            session=session, document_ids=[result.id]
        )
    await session.commit()
    return _build_document_upload_response(result)


@router.post(
    "/{bank_id}/documents/batch",
    response_model=BankDocumentBatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_bank_document_batch(
    bank_id: UUID,
    payload: BankDocumentBatchUploadRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
    embedder: BankDocumentEmbedderService | None = Depends(get_bank_document_embedder),
    fact_extractor: BankFactExtractorService | None = Depends(get_bank_fact_extractor),
) -> BankDocumentBatchUploadResponse:
    del _csrf
    await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    documents = [_build_document_input(item) for item in payload.documents]
    _validate_batch_source_keys(documents)
    result = await _bank_indexer.upsert_documents(
        session=session,
        bank_id=bank_id,
        documents=documents,
    )
    if embedder is not None:
        await embedder.embed_documents(
            session=session,
            document_ids=[item.id for item in result.items],
        )
    if fact_extractor is not None:
        changed_document_ids = [item.id for item in result.items if not item.unchanged]
        if changed_document_ids:
            await fact_extractor.extract_documents(
                session=session,
                document_ids=changed_document_ids,
            )
    await session.commit()
    return BankDocumentBatchUploadResponse(
        items=[_build_document_upload_response(item) for item in result.items],
        indexed_documents=result.indexed_documents,
        indexed_chunks=result.indexed_chunks,
        unchanged_documents=result.unchanged_documents,
    )


@router.get(
    "/{bank_id}/documents/{document_id}", response_model=BankDocumentDetailResponse
)
async def get_bank_document(
    bank_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
) -> BankDocumentDetailResponse:
    await _require_bank_access(
        session=session, bank_id=bank_id, current_user=current_user
    )
    document = await _bank_query_service.get_document(
        session=session,
        bank_id=bank_id,
        document_id=document_id,
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Bank document not found")
    return BankDocumentDetailResponse(
        id=document.id,
        bank_id=document.bank_id,
        title=document.title,
        source_kind=document.source_kind,
        source_key=document.source_key,
        external_id=document.external_id,
        content=document.content,
        bytes=document.bytes,
        chunks=[
            BankDocumentChunkResponse(
                chunk_index=chunk.chunk_index,
                heading_path=chunk.heading_path,
            )
            for chunk in document.chunks
        ],
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.delete(
    "/{bank_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_bank_document(
    bank_id: UUID,
    document_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf
    await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    document = await session.scalar(
        select(BankDocument).where(
            BankDocument.bank_id == bank_id,
            BankDocument.id == document_id,
        )
    )
    if document is None:
        raise ApiError(404, "NOT_FOUND", "Bank document not found")
    await session.delete(document)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{bank_id}/sync")
async def sync_bank(
    bank_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf
    await _require_bank_owner_or_admin(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
    raise ApiError(
        status.HTTP_501_NOT_IMPLEMENTED,
        "NOT_IMPLEMENTED",
        "Bank sync is not available in MVP",
    )


def _build_bank_detail_response(detail) -> BankDetailResponse:
    return BankDetailResponse(
        id=detail.id,
        name=detail.name,
        description=detail.description,
        owner_id=detail.owner_id,
        documents=BankDocumentsPageResponse(
            items=[
                BankDocumentListItemResponse(
                    id=item.id,
                    title=item.title,
                    source_kind=item.source_kind,
                    source_key=item.source_key,
                    external_id=item.external_id,
                    bytes=item.bytes,
                    chunk_count=item.chunk_count,
                    updated_at=item.updated_at,
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


def _build_document_input(
    payload: BankDocumentUploadRequest,
) -> BankDocumentUpsertInput:
    source_key = payload.source_key.strip()
    if not source_key:
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
    _validate_document_content(content=payload.content)
    return BankDocumentUpsertInput(
        source_key=source_key,
        title=payload.title,
        content=payload.content,
    )


def _build_document_upload_response(result) -> BankDocumentUploadResponse:
    return BankDocumentUploadResponse(
        id=result.id,
        bank_id=result.bank_id,
        title=result.title,
        source_kind=result.source_kind.value,
        source_key=result.source_key,
        external_id=result.external_id,
        bytes=result.bytes,
        chunk_count=result.chunk_count,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


async def _parse_single_document_upload(request: Request) -> BankDocumentUpsertInput:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = BankDocumentUploadRequest.model_validate(await request.json())
        return _build_document_input(payload)
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
        content = await _read_upload_content(upload)  # type: ignore[arg-type]
        return BankDocumentUpsertInput(
            source_key=_require_source_key(source_key),
            title=title,
            filename=upload.filename,
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
    if len(content_bytes) > _MAX_BANK_DOCUMENT_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Document exceeds 10 MB limit")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(
            415, "UNSUPPORTED_MEDIA_TYPE", "Document must be UTF-8 text"
        ) from exc
    _validate_document_content(content=content)
    return content


def _validate_document_content(*, content: str) -> None:
    if len(content.encode("utf-8")) > _MAX_BANK_DOCUMENT_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Document exceeds 10 MB limit")


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


def _require_bank_name(name: str) -> str:
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
    if len(normalized) > _MAX_BANK_NAME_LENGTH:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Request validation failed",
            field_errors=[
                FieldError(
                    field="name",
                    code="INVALID",
                    message=f"name must be at most {_MAX_BANK_NAME_LENGTH} characters",
                )
            ],
        )
    return normalized


def _validate_batch_source_keys(documents: list[BankDocumentUpsertInput]) -> None:
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


def _is_supported_upload(*, suffix: str, content_type: str | None) -> bool:
    if suffix in _ALLOWED_UPLOAD_SUFFIXES:
        return True
    if not suffix and content_type in _TEXTUAL_UPLOAD_CONTENT_TYPES:
        return True
    return False


async def _require_bank_access(
    *,
    session: AsyncSession,
    bank_id: UUID,
    current_user: User,
) -> Bank:
    return await get_readable_bank(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )


async def _require_bank_owner_or_admin(
    *,
    session: AsyncSession,
    bank_id: UUID,
    current_user: User,
) -> Bank:
    return await _require_bank_access(
        session=session,
        bank_id=bank_id,
        current_user=current_user,
    )
