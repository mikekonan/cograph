from __future__ import annotations

import logging

from backend.app.config import Settings
from backend.app.llm.embedder import EmbedProvider
from backend.app.rag.hybrid import HybridRetriever, VectorRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.rerank import NullReranker, make_reranker
from backend.app.rag.router import RerankRouter

logger = logging.getLogger(__name__)


def build_query_embed_provider(settings: Settings) -> EmbedProvider | None:
    if not settings.embedding.enabled:
        return None

    from backend.app.llm.embedder import OpenAIEmbedProvider

    return OpenAIEmbedProvider(
        api_url=settings.embedding.api_url,
        api_key=settings.embedding.api_key.get_secret_value(),
        model=settings.embedding.model,
        dimensions=settings.embedding.dimensions,
    )


def build_hybrid_retriever(settings: Settings) -> HybridRetriever:
    try:
        reranker = make_reranker(settings.retrieval.rerank.model_dump())
    except (ImportError, NotImplementedError, ValueError) as exc:
        logger.warning("Retrieval reranker unavailable; falling back to NullReranker", exc_info=exc)
        reranker = NullReranker()

    return HybridRetriever(
        vector=VectorRetriever(),
        lexical=LexicalRetriever(),
        symbol=SymbolLookup(),
        reranker=reranker,
        router=RerankRouter(
            rerank_threshold=settings.retrieval.rerank.threshold,
            enabled=settings.retrieval.rerank.enabled,
        ),
        rrf_k=settings.retrieval.rrf_k,
        candidate_cap=settings.retrieval.candidate_cap,
    )
