from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.bank_access import ensure_readable_banks
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    get_settings_dep,
)
from backend.app.core.errors import ApiError
from backend.app.core.repository_access import (
    get_readable_repository,
    get_readable_repository_by_slug,
)
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.enums import RepositoryStatus
from backend.app.models.user import User
from backend.app.rag.blended_search import BlendedSearchResponse, BlendedSearchService
from backend.app.rag.context_builder import (
    ContextBuilder,
    RetrievalLayer,
    RetrievalResponse,
)
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.runtime import build_hybrid_retriever
from backend.app.rag.service import retrieve_composite

router = APIRouter(tags=["retrieval"])


class RetrievalIncludeRequest(BaseModel):
    chunks: bool = True
    graph: bool = False
    scores: bool = False


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    repository_id: UUID | None = None
    bank_ids: list[UUID] | None = None
    stores: list[RetrievalLayer] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    as_of: datetime | None = None
    since: datetime | None = None
    until: datetime | None = None
    include: RetrievalIncludeRequest = Field(default_factory=RetrievalIncludeRequest)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @model_validator(mode="after")
    def _validate_temporal_window(self) -> "RetrievalRequest":
        if (
            self.since is not None
            and self.until is not None
            and self.since > self.until
        ):
            raise ValueError("since must be earlier than or equal to until")
        if (
            self.since is not None
            and self.as_of is not None
            and self.since > self.as_of
        ):
            raise ValueError("since must be earlier than or equal to as_of")
        return self


class BlendedSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


async def get_query_embed_provider(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    providers = await build_runtime_providers(
        session=session,
        settings=request.app.state.settings,
    )
    return providers.embed_provider


def get_hybrid_retriever(request: Request) -> HybridRetriever:
    return build_hybrid_retriever(request.app.state.settings)


def get_context_builder() -> ContextBuilder:
    return ContextBuilder()


def get_lexical_retriever() -> LexicalRetriever:
    return LexicalRetriever()


def get_symbol_lookup() -> SymbolLookup:
    return SymbolLookup()


def get_blended_search_service(
    settings: Settings = Depends(get_settings_dep),
    lexical: LexicalRetriever = Depends(get_lexical_retriever),
    symbol: SymbolLookup = Depends(get_symbol_lookup),
) -> BlendedSearchService:
    return BlendedSearchService(
        lexical=lexical,
        symbol=symbol,
        rrf_k=settings.retrieval.rrf_k,
        candidate_cap=settings.retrieval.candidate_cap,
    )


@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve(
    payload: RetrievalRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
    embed_provider=Depends(get_query_embed_provider),
    retriever: HybridRetriever = Depends(get_hybrid_retriever),
    context_builder: ContextBuilder = Depends(get_context_builder),
) -> RetrievalResponse:
    if payload.repository_id is not None:
        await get_readable_repository(
            session=session,
            repository_id=payload.repository_id,
            settings=settings,
            current_user=current_user,
        )
    await ensure_readable_banks(
        session=session,
        bank_ids=payload.bank_ids,
        current_user=current_user,
    )
    return await retrieve_composite(
        session,
        query=payload.query,
        repository_id=payload.repository_id,
        bank_ids=payload.bank_ids,
        requested_layers=set(payload.stores) if payload.stores else set(RetrievalLayer),
        top_k=payload.top_k,
        as_of=payload.as_of,
        since=payload.since,
        until=payload.until,
        include_chunks=payload.include.chunks,
        include_graph=payload.include.graph,
        include_scores=payload.include.scores,
        embed_provider=embed_provider,
        retriever=retriever,
        context_builder=context_builder,
    )


@router.post("/repos/{host}/{owner}/{name}/search", response_model=BlendedSearchResponse)
async def search_repository(
    host: str,
    owner: str,
    name: str,
    payload: BlendedSearchRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
    search_service: BlendedSearchService = Depends(get_blended_search_service),
) -> BlendedSearchResponse:
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ApiError(409, "REPO_NOT_READY", "Repository is not ready yet")
    return await search_service.search(
        session,
        repository_id=repository.id,
        repo_slug_path=f"{repository.host}/{repository.owner}/{repository.name}",
        query=payload.query,
        top_k=payload.top_k,
    )
