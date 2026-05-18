from __future__ import annotations

import time
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    get_settings_dep,
)
from backend.app.core.repository_access import get_readable_repository
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.enums import QueryLogSource, QueryLogStatus
from backend.app.models.user import User
from backend.app.query_logs import enqueue_query_log
from backend.app.rag.context_builder import (
    ContextBuilder,
    RetrievalLayer,
    RetrievalResponse,
)
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.runtime import build_hybrid_retriever
from backend.app.rag.service import retrieve_composite
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
)


def _result_count(response: RetrievalResponse) -> int:
    return len(response.results)


def _client_label(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:128]


router = APIRouter(tags=["retrieval"])


class RetrievalIncludeRequest(BaseModel):
    chunks: bool = True
    graph: bool = False
    scores: bool = False


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    repository_id: UUID | None = None
    stores: list[RetrievalLayer] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    snippet_chars: int = Field(
        default=DEFAULT_SNIPPET_CHARS,
        ge=MIN_SNIPPET_CHARS,
        le=MAX_SNIPPET_CHARS,
    )
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


@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve(
    request: Request,
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

    started = time.perf_counter()
    status = QueryLogStatus.ERROR
    error_code: str | None = None
    result_count: int | None = None
    response: RetrievalResponse | None = None
    usage_sink: dict = {}
    try:
        response = await retrieve_composite(
            session,
            query=payload.query,
            repository_id=payload.repository_id,
            requested_layers=set(payload.stores)
            if payload.stores
            else set(RetrievalLayer),
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
            snippet_chars=payload.snippet_chars,
            usage_sink=usage_sink,
        )
        result_count = _result_count(response)
        status = QueryLogStatus.OK if result_count > 0 else QueryLogStatus.EMPTY
        return response
    except Exception as exc:
        error_code = type(exc).__name__
        raise
    finally:
        if current_user is not None:
            await enqueue_query_log(
                app_state=request.app.state,
                user_id=current_user.id,
                user_email=current_user.email,
                source=QueryLogSource.REST,
                tool_name="rest.retrieve",
                query_text=payload.query,
                repository_id=payload.repository_id,
                top_k=payload.top_k,
                result_count=result_count,
                duration_ms=int((time.perf_counter() - started) * 1000),
                status=status,
                error_code=error_code,
                client_label=_client_label(request),
                tokens_input=usage_sink.get("tokens_input"),
                tokens_output=usage_sink.get("tokens_output"),
                embed_model=usage_sink.get("embed_model"),
                completion_model=usage_sink.get("completion_model"),
            )
