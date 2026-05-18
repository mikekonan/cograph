from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import ApiError
from backend.app.llm.embedder import EmbedProvider, EmbeddingProviderError
from backend.app.models.enums import RepositoryStatus
from backend.app.models.repository import Repository
from backend.app.rag.context_builder import ContextBuilder, RetrievalLayer, RetrievalResponse
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.snippet import DEFAULT_SNIPPET_CHARS


async def retrieve_composite(
    session: AsyncSession,
    *,
    query: str,
    repository_id: UUID | None,
    requested_layers: set[RetrievalLayer],
    top_k: int,
    as_of: datetime | None,
    since: datetime | None,
    until: datetime | None,
    include_chunks: bool,
    include_graph: bool,
    include_scores: bool,
    embed_provider: EmbedProvider | None,
    retriever: HybridRetriever,
    context_builder: ContextBuilder,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    mode: str | None = None,
    usage_sink: dict | None = None,
) -> RetrievalResponse:
    """If `usage_sink` is provided, the embed call writes
    `embed_model` and `tokens_input` into it (additive — repeated
    calls accumulate `tokens_input`). MCP / REST wrappers use this
    to feed the query_log bucket without changing the response shape.
    """
    validate_retrieval_scope(
        repository_id=repository_id,
        requested_layers=requested_layers,
    )

    uses_repository = bool(
        {
            RetrievalLayer.AST,
            RetrievalLayer.CODE,
            RetrievalLayer.AST_SUMMARY,
            RetrievalLayer.REPO_DOC,
        }
        & requested_layers
    )
    if repository_id is not None and uses_repository:
        repository = await session.get(Repository, repository_id)
        if repository is None:
            raise ApiError(404, "NOT_FOUND", "Repository not found")
        if repository.status is not RepositoryStatus.READY:
            return RetrievalResponse(results=[], nodes={})

    if embed_provider is None:
        raise ApiError(
            503,
            "RETRIEVAL_UNAVAILABLE",
            "Retrieval requires an embedding provider to be configured",
        )

    try:
        # `embed_with_usage` is part of the protocol but third-party
        # duck-typed stubs (notably the test doubles) implement only
        # the bare `embed`. Fall back to it; in that case we have no
        # usage data, so the cost column stays NULL.
        if hasattr(embed_provider, "embed_with_usage"):
            vectors, usage = await embed_provider.embed_with_usage([query])
            query_embedding = vectors[0]
            if usage_sink is not None:
                usage_sink["embed_model"] = usage.model
                usage_sink["tokens_input"] = int(
                    (usage_sink.get("tokens_input") or 0) + usage.tokens_input
                )
        else:
            query_embedding = (await embed_provider.embed([query]))[0]
    except EmbeddingProviderError as exc:
        raise ApiError(
            503,
            "EMBEDDING_PROVIDER_FAILED",
            "Embedding provider unavailable",
        ) from exc

    chunks = await retriever.retrieve(
        session,
        query_text=query,
        query_embedding=query_embedding,
        repository_id=repository_id,
        top_k=top_k,
        as_of=as_of,
        since=since,
        until=until,
        stores=engine_stores_from_layers(requested_layers),
    )
    return await context_builder.build(
        session,
        chunks=chunks,
        requested_layers=requested_layers,
        repository_id=repository_id,
        include_chunks=include_chunks,
        include_graph=include_graph,
        include_scores=include_scores,
        query=query,
        snippet_chars=snippet_chars,
        mode=mode,
    )


def engine_stores_from_layers(layers: set[RetrievalLayer]) -> set[str]:
    stores: set[str] = set()
    if {
        RetrievalLayer.AST,
        RetrievalLayer.CODE,
        RetrievalLayer.AST_SUMMARY,
    } & layers:
        stores.add("code")
    if RetrievalLayer.REPO_DOC in layers:
        stores.add("repo_docs")
    return stores


def validate_retrieval_scope(
    *,
    repository_id: UUID | None,
    requested_layers: set[RetrievalLayer],
) -> None:
    if not repository_id:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "repository_id is required",
        )

    uses_repository = bool(
        {
            RetrievalLayer.AST,
            RetrievalLayer.CODE,
            RetrievalLayer.AST_SUMMARY,
            RetrievalLayer.REPO_DOC,
        }
        & requested_layers
    )
    if uses_repository and repository_id is None:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "repository_id is required for code or repo_doc retrieval",
        )
