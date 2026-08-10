"""Plumbing layer: wraps `backend.app.rag` retrievers for prompt-side use.

The retrievers themselves stay unchanged. This module's only job is to convert
prompt needs (page purpose + source hints) into typed `PageBundle`s that the
writer prompt can consume.

Retrieval shape:

* For the overview stage, no retrieval call is made — `for_overview` just
  re-exposes the top-N rows from `code_node_summaries` already loaded by
  `context.py`.
* For each wiki page, `for_page` embeds the page purpose (concatenated with
  any `sources_hint` strings to bias retrieval), runs `HybridRetriever`
  separately over the `code` and `repo_docs` stores (so we get a fixed
  budget per store), batch-fetches summaries for the returned code nodes,
  and pivots the top code hits via `GraphPivot.expand` to surface 1-hop
  callers/callees/parents.
"""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import EmbedProvider
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.pivot import GraphPivot, PivotNode, PivotRelatedNode
from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.concept_match import apply_domain_rerank
from backend.app.wiki.context import TopSummary
from backend.app.wiki.schemas import (
    BusinessContextConfidence,
    DomainConcept,
)

logger = logging.getLogger(__name__)


class CodeChunk(BaseModel):
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    summary: str | None = None
    snippet: str
    code_node_id: UUID
    # 1-indexed position in the per-store retrieval result. The writer
    # prompt uses rank+score to decide which chunks to lean on; lowest
    # rank ⇒ best match.
    rank: int = 0
    score: float = 0.0


class DocChunk(BaseModel):
    file_path: str
    title: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    chunk_index: int
    snippet: str
    chunk_id: UUID
    rank: int = 0
    score: float = 0.0


class GraphNeighbor(BaseModel):
    qualified_name: str
    node_type: str
    file_path: str
    start_line: int
    role: str
    code_node_id: UUID


class OverviewBundle(BaseModel):
    """Pulled directly from `code_node_summaries` for Stage 2 (no retrieval call)."""

    top_summaries: list[TopSummary]
    code_node_count: int


class PageBundle(BaseModel):
    """Per-page retrieval payload for Stage 4."""

    code_chunks: list[CodeChunk] = Field(default_factory=list)
    doc_chunks: list[DocChunk] = Field(default_factory=list)
    graph_neighbors: list[GraphNeighbor] = Field(default_factory=list)


_SNIPPET_CHAR_CAP = 1_200


class WikiRetrievalService:
    """Adapter from prompt needs to existing RAG retrievers.

    Reuses `HybridRetriever`, `GraphPivot`, and an `EmbedProvider`. No new
    retrieval code lives here — this is just plumbing to pack results into
    `PageBundle`s the writer prompt can consume.
    """

    def __init__(
        self,
        *,
        hybrid: HybridRetriever,
        embedder: EmbedProvider,
        pivot: GraphPivot | None = None,
    ) -> None:
        self._hybrid = hybrid
        self._embedder = embedder
        self._pivot = pivot or GraphPivot()

    @property
    def hybrid(self) -> HybridRetriever:
        return self._hybrid

    @property
    def embedder(self) -> EmbedProvider:
        return self._embedder

    async def for_overview(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        top_n: int = 30,
    ) -> OverviewBundle:
        """Pull top-N summaries + total code_node count. No retrieval call.

        Importing `_load_top_summaries` from `context` would couple the two
        modules at private API. Instead, we delegate via `build_repo_context`
        callers when a `RepoContext` is already in hand; this method is a
        small fallback for callers that only have a `(session, repo_id)`.
        """
        from backend.app.wiki.context import (
            _load_top_summaries,  # noqa: PLC2701 — sibling private helper
        )
        from backend.app.models.code_node import CodeNode
        from sqlalchemy import func

        top_summaries = await _load_top_summaries(
            session=session,
            repository_id=repository_id,
            cap=top_n,
        )
        total = await session.scalar(
            select(func.count(CodeNode.id)).where(
                CodeNode.repository_id == repository_id
            )
        )
        return OverviewBundle(
            top_summaries=top_summaries,
            code_node_count=int(total or 0),
        )

    async def for_page(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        purpose: str,
        sources_hint: list[str],
        code_top_k: int = 12,
        docs_top_k: int = 6,
        graph_pivot_top_k: int = 5,
        domain_concepts: list[DomainConcept] | None = None,
        business_confidence: BusinessContextConfidence | None = None,
    ) -> PageBundle:
        """Build a `PageBundle` for one wiki page.

        Steps:
            1. Build a query string by concatenating `purpose` and
               `sources_hint` so symbol/file names bias retrieval.
            2. Embed the query once.
            3. Run `HybridRetriever.retrieve` once over `code` and once over
               `repo_docs` so each store gets a fixed budget.
            4. Batch-fetch `code_node_summaries.summary` for code hits.
            5. Pivot the top `graph_pivot_top_k` code hits via `GraphPivot.expand`.
            6. Pack into `PageBundle`.

        Returns an empty bundle on any retrieval-side failure — the writer
        prompt is written to handle a sparse context.
        """
        query_text = _compose_query_text(purpose=purpose, sources_hint=sources_hint)
        try:
            embedding = (await self._embedder.embed([query_text]))[0]
        except Exception as exc:  # pragma: no cover — exercised in integration runs
            logger.warning(
                "for_page: embedding failed (%s); returning empty bundle", exc
            )
            return PageBundle()

        code_hits = await _safe_retrieve(
            self._hybrid,
            session=session,
            query_text=query_text,
            query_embedding=embedding,
            repository_id=repository_id,
            top_k=code_top_k,
            store="code",
        )
        doc_hits = await _safe_retrieve(
            self._hybrid,
            session=session,
            query_text=query_text,
            query_embedding=embedding,
            repository_id=repository_id,
            top_k=docs_top_k,
            store="repo_docs",
        )

        # T6: domain-concept-aware rerank — additive boost on max-norm
        # scores. No-op when the BusinessContext has no domain concepts.
        code_hits = apply_domain_rerank(
            code_hits, concepts=domain_concepts, confidence=business_confidence
        )
        doc_hits = apply_domain_rerank(
            doc_hits, concepts=domain_concepts, confidence=business_confidence
        )

        code_node_ids = [hit.chunk_id for hit in code_hits]
        summaries_by_id = await _load_summaries_for_nodes(
            session=session,
            code_node_ids=code_node_ids,
        )

        code_chunks = [
            CodeChunk(
                qualified_name=str(hit.metadata.get("qualified_name", "")),
                file_path=str(hit.metadata.get("file_path", "")),
                start_line=int(hit.metadata.get("start_line") or 0),
                end_line=int(hit.metadata.get("end_line") or 0),
                language=str(hit.metadata.get("language", "")),
                summary=summaries_by_id.get(hit.chunk_id),
                snippet=_truncate(hit.content, _SNIPPET_CHAR_CAP),
                code_node_id=hit.chunk_id,
                rank=rank,
                score=float(hit.score),
            )
            for rank, hit in enumerate(code_hits, start=1)
        ]
        doc_chunks = [
            DocChunk(
                file_path=str(hit.metadata.get("file_path", "")),
                title=hit.metadata.get("title"),
                heading_path=list(hit.metadata.get("heading_path") or []),
                chunk_index=int(hit.metadata.get("chunk_index") or 0),
                snippet=_truncate(hit.content, _SNIPPET_CHAR_CAP),
                chunk_id=hit.chunk_id,
                rank=rank,
                score=float(hit.score),
            )
            for rank, hit in enumerate(doc_hits, start=1)
        ]

        graph_neighbors: list[GraphNeighbor] = []
        if code_node_ids:
            try:
                pivots = await self._pivot.expand(
                    session=session,
                    repository_id=repository_id,
                    node_ids=code_node_ids[:graph_pivot_top_k],
                )
                graph_neighbors = _flatten_pivots(pivots)
            except Exception as exc:  # pragma: no cover — exercised in integration runs
                logger.warning(
                    "for_page: graph pivot failed (%s); skipping neighbors", exc
                )

        return PageBundle(
            code_chunks=code_chunks,
            doc_chunks=doc_chunks,
            graph_neighbors=graph_neighbors,
        )

    async def for_section(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        page_purpose: str,
        section_heading: str,
        sources_hint: list[str] | None = None,
        code_top_k: int = 8,
        docs_top_k: int = 4,
        graph_pivot_top_k: int = 0,
        domain_concepts: list[DomainConcept] | None = None,
        business_confidence: BusinessContextConfidence | None = None,
    ) -> PageBundle:
        """Section-scoped retrieval — tighter top_k, anchored on the heading.

        Uses `f"{page_purpose} :: {section_heading}"` as the embedding query
        so the result skews toward the section's specific concern instead of
        re-pulling the whole page's payload. Defaults skip graph pivots
        (the section text already names the symbols it cares about; pivots
        bloat the bundle for a single H2).
        """
        composite_purpose = (
            f"{(page_purpose or '').strip()} :: {section_heading.strip()}"
        )
        return await self.for_page(
            session=session,
            repository_id=repository_id,
            purpose=composite_purpose,
            sources_hint=list(sources_hint or []),
            code_top_k=code_top_k,
            docs_top_k=docs_top_k,
            graph_pivot_top_k=graph_pivot_top_k,
            domain_concepts=domain_concepts,
            business_confidence=business_confidence,
        )


def _compose_query_text(*, purpose: str, sources_hint: list[str]) -> str:
    purpose = (purpose or "").strip()
    hints = [h.strip() for h in (sources_hint or []) if h and h.strip()]
    if not hints:
        return purpose
    return f"{purpose}\n\nRelevant identifiers: {', '.join(hints)}"


def _truncate(text: str, cap: int) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap] + "…"


async def _safe_retrieve(
    hybrid: HybridRetriever,
    *,
    session: AsyncSession,
    query_text: str,
    query_embedding: list[float],
    repository_id: UUID,
    top_k: int,
    store: str,
) -> list[RetrievedChunk]:
    try:
        return await hybrid.retrieve(
            session,
            query_text=query_text,
            query_embedding=query_embedding,
            repository_id=repository_id,
            top_k=top_k,
            stores={store},  # type: ignore[arg-type]
        )
    except Exception as exc:  # pragma: no cover — exercised in integration runs
        logger.warning(
            "for_page: hybrid retrieve over %s failed (%s); returning no hits",
            store,
            exc,
        )
        return []


async def _load_summaries_for_nodes(
    *,
    session: AsyncSession,
    code_node_ids: list[UUID],
) -> dict[UUID, str]:
    if not code_node_ids:
        return {}
    stmt = select(CodeNodeSummary.code_node_id, CodeNodeSummary.summary).where(
        CodeNodeSummary.code_node_id.in_(code_node_ids)
    )
    rows = (await session.execute(stmt)).all()
    return {row[0]: row[1] for row in rows if row[1]}


def _flatten_pivots(pivots: dict[UUID, PivotNode]) -> list[GraphNeighbor]:
    neighbors: list[GraphNeighbor] = []
    seen: set[tuple[UUID, str]] = set()

    def _push(node: PivotRelatedNode | None, *, role: str) -> None:
        if node is None:
            return
        key = (node.id, role)
        if key in seen:
            return
        seen.add(key)
        neighbors.append(
            GraphNeighbor(
                qualified_name=node.name,
                node_type=node.node_type.value,
                file_path=node.file_path,
                start_line=int(node.start_line or 0),
                role=role,
                code_node_id=node.id,
            )
        )

    for pivot in pivots.values():
        if pivot.parent is not None:
            _push(pivot.parent, role="parent")
        for caller in pivot.callers:
            _push(caller, role="caller")
        for callee in pivot.callees:
            _push(callee, role="callee")
    return neighbors
