"""Composite retrieval response builder for Phase 7e."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.code_subgraph_summary import CodeSubgraphSummary
from backend.app.models.enums import CodeNodeType
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repo_document_chunk_mention import RepoDocumentChunkMention
from backend.app.rag.pivot import GraphPivot, PivotNode, PivotRelatedNode
from backend.app.rag.retriever import RetrievedChunk


class RetrievalLayer(StrEnum):
    AST = "ast"
    CODE = "code"
    AST_SUMMARY = "ast_summary"
    REPO_DOC = "repo_doc"


class CandidateFrom(StrEnum):
    VECTOR = "vector"
    LEXICAL = "lexical"
    SYMBOL = "symbol"
    GRAPH = "graph"


class LinkedRepoDocumentChunk(BaseModel):
    chunk_id: UUID
    document_id: UUID
    file_path: str
    title: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    snippet: str


class RetrievalProvenance(BaseModel):
    node_id: UUID | None = None
    qualified_name: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    document_id: UUID | None = None
    heading_path: list[str] | None = None
    first_seen_commit: str | None = None
    last_changed_commit: str | None = None
    last_changed_at: str | None = None


class RetrievalMetadata(BaseModel):
    vector_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None
    candidate_from: list[CandidateFrom] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    layer: RetrievalLayer
    score: float | None = None
    snippet: str
    provenance: RetrievalProvenance
    metadata: RetrievalMetadata
    related_repo_doc_chunks: list[LinkedRepoDocumentChunk] = Field(default_factory=list)


class RetrievalRelatedNode(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None


class RetrievalGraphNode(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    summary: str | None = None
    callers: list[RetrievalRelatedNode] = Field(default_factory=list)
    callees: list[RetrievalRelatedNode] = Field(default_factory=list)
    parent: RetrievalRelatedNode | None = None


class RetrievalResponse(BaseModel):
    results: list[RetrievalResult]
    nodes: dict[str, RetrievalGraphNode] = Field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class RepoDocChunkRecord:
    chunk_id: UUID
    document_id: UUID
    file_path: str
    title: str | None
    heading_path: list[str]
    content: str


class ContextBuilder:
    def __init__(
        self,
        *,
        graph_pivot: GraphPivot | None = None,
        related_chunk_limit: int = 3,
    ) -> None:
        self.graph_pivot = graph_pivot or GraphPivot()
        self.related_chunk_limit = int(related_chunk_limit)

    async def build(
        self,
        session: AsyncSession,
        *,
        chunks: list[RetrievedChunk],
        requested_layers: set[RetrievalLayer],
        repository_id: UUID | None,
        include_chunks: bool,
        include_graph: bool,
        include_scores: bool,
    ) -> RetrievalResponse:
        code_hits = [chunk for chunk in chunks if chunk.store == "code"]
        repo_doc_hits = [chunk for chunk in chunks if chunk.store == "repo_docs"]

        code_node_ids = [chunk.chunk_id for chunk in code_hits]
        repo_doc_chunk_ids = [chunk.chunk_id for chunk in repo_doc_hits]

        code_nodes_by_id = await self._load_code_nodes(session, code_node_ids)
        summaries_by_id = await self._load_node_summaries(
            session,
            repository_id=repository_id,
            node_ids=code_node_ids,
        )
        linked_docs_by_node = (
            await self._load_linked_repo_doc_chunks(session, code_node_ids)
            if include_chunks
            else {}
        )
        repo_doc_chunks_by_id = await self._load_repo_doc_chunks(session, repo_doc_chunk_ids)

        graph_nodes: dict[UUID, PivotNode] = {}
        if include_graph and repository_id is not None and code_node_ids:
            graph_nodes = await self.graph_pivot.expand(
                session=session,
                repository_id=repository_id,
                node_ids=code_node_ids,
            )

        results: list[RetrievalResult] = []
        for chunk in chunks:
            if chunk.store == "code":
                node = code_nodes_by_id.get(chunk.chunk_id)
                if node is None:
                    continue
                related_docs = linked_docs_by_node.get(node.id, [])
                if RetrievalLayer.CODE in requested_layers:
                    results.append(
                        RetrievalResult(
                            layer=RetrievalLayer.CODE,
                            score=chunk.score if include_scores else None,
                            snippet=_snippet(node.content),
                            provenance=_code_provenance(node),
                            metadata=_chunk_metadata(chunk, include_scores=include_scores),
                            related_repo_doc_chunks=related_docs,
                        )
                    )
                if RetrievalLayer.AST in requested_layers:
                    results.append(
                        RetrievalResult(
                            layer=RetrievalLayer.AST,
                            score=chunk.score if include_scores else None,
                            snippet=_snippet(node.signature or node.qualified_name or node.name),
                            provenance=_code_provenance(node),
                            metadata=_chunk_metadata(
                                chunk,
                                include_scores=include_scores,
                                add_graph_origin=include_graph,
                            ),
                            related_repo_doc_chunks=related_docs,
                        )
                    )
                summary = summaries_by_id.get(node.id)
                if summary and RetrievalLayer.AST_SUMMARY in requested_layers:
                    results.append(
                        RetrievalResult(
                            layer=RetrievalLayer.AST_SUMMARY,
                            score=chunk.score if include_scores else None,
                            snippet=_snippet(summary),
                            provenance=_code_provenance(node),
                            metadata=_chunk_metadata(chunk, include_scores=include_scores),
                            related_repo_doc_chunks=related_docs,
                        )
                    )
                continue

            if chunk.store == "repo_docs" and RetrievalLayer.REPO_DOC in requested_layers:
                repo_doc_chunk = repo_doc_chunks_by_id.get(chunk.chunk_id)
                if repo_doc_chunk is None:
                    continue
                results.append(
                    RetrievalResult(
                        layer=RetrievalLayer.REPO_DOC,
                        score=chunk.score if include_scores else None,
                        snippet=_snippet(repo_doc_chunk.content),
                        provenance=RetrievalProvenance(
                            document_id=repo_doc_chunk.document_id,
                            file_path=repo_doc_chunk.file_path,
                            heading_path=list(repo_doc_chunk.heading_path),
                        ),
                        metadata=_chunk_metadata(chunk, include_scores=include_scores),
                    )
                )

        return RetrievalResponse(
            results=results,
            nodes={
                str(node_id): RetrievalGraphNode(
                    id=node.id,
                    name=node.name,
                    node_type=node.node_type,
                    language=node.language,
                    file_path=node.file_path,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    signature=node.signature,
                    summary=summaries_by_id.get(node_id),
                    callers=[_related_node_model(item) for item in node.callers],
                    callees=[_related_node_model(item) for item in node.callees],
                    parent=_related_node_model(node.parent) if node.parent is not None else None,
                )
                for node_id, node in graph_nodes.items()
            },
        )

    async def _load_code_nodes(
        self,
        session: AsyncSession,
        node_ids: list[UUID],
    ) -> dict[UUID, CodeNode]:
        if not node_ids:
            return {}
        rows = (
            await session.scalars(select(CodeNode).where(CodeNode.id.in_(list(dict.fromkeys(node_ids)))))
        ).all()
        return {row.id: row for row in rows}

    async def _load_node_summaries(
        self,
        session: AsyncSession,
        *,
        repository_id: UUID | None,
        node_ids: list[UUID],
    ) -> dict[UUID, str]:
        if not node_ids:
            return {}

        unique_ids = list(dict.fromkeys(node_ids))
        summaries = {
            row.code_node_id: row.summary
            for row in (
                await session.scalars(
                    select(CodeNodeSummary).where(CodeNodeSummary.code_node_id.in_(unique_ids))
                )
            ).all()
        }
        if repository_id is None:
            return summaries

        subgraph_rows = (
            await session.scalars(
                select(CodeSubgraphSummary).where(
                    CodeSubgraphSummary.repository_id == repository_id,
                    CodeSubgraphSummary.root_node_id.in_(unique_ids),
                )
            )
        ).all()
        for row in subgraph_rows:
            summaries.setdefault(row.root_node_id, row.summary)
        return summaries

    async def _load_linked_repo_doc_chunks(
        self,
        session: AsyncSession,
        node_ids: list[UUID],
    ) -> dict[UUID, list[LinkedRepoDocumentChunk]]:
        if not node_ids:
            return {}

        rows = (
            await session.execute(
                select(
                    RepoDocumentChunkMention.code_node_id,
                    RepoDocumentChunk.id,
                    RepoDocumentChunk.document_id,
                    RepoDocument.file_path,
                    RepoDocument.title,
                    RepoDocumentChunk.heading_path,
                    RepoDocumentChunk.content,
                    RepoDocumentChunk.chunk_index,
                )
                .join(RepoDocumentChunk, RepoDocumentChunk.id == RepoDocumentChunkMention.chunk_id)
                .join(RepoDocument, RepoDocument.id == RepoDocumentChunk.document_id)
                .where(RepoDocumentChunkMention.code_node_id.in_(list(dict.fromkeys(node_ids))))
                .order_by(
                    RepoDocumentChunkMention.code_node_id,
                    RepoDocument.file_path,
                    RepoDocumentChunk.chunk_index,
                )
            )
        ).all()

        grouped: dict[UUID, list[LinkedRepoDocumentChunk]] = {}
        for code_node_id, chunk_id, document_id, file_path, title, heading_path, content, _ in rows:
            bucket = grouped.setdefault(code_node_id, [])
            if len(bucket) >= self.related_chunk_limit:
                continue
            bucket.append(
                LinkedRepoDocumentChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    file_path=file_path,
                    title=title,
                    heading_path=list(heading_path or []),
                    snippet=_snippet(content),
                )
            )
        return grouped

    async def _load_repo_doc_chunks(
        self,
        session: AsyncSession,
        chunk_ids: list[UUID],
    ) -> dict[UUID, RepoDocChunkRecord]:
        if not chunk_ids:
            return {}
        rows = (
            await session.execute(
                select(
                    RepoDocumentChunk.id,
                    RepoDocumentChunk.document_id,
                    RepoDocument.file_path,
                    RepoDocument.title,
                    RepoDocumentChunk.heading_path,
                    RepoDocumentChunk.content,
                )
                .join(RepoDocument, RepoDocument.id == RepoDocumentChunk.document_id)
                .where(RepoDocumentChunk.id.in_(list(dict.fromkeys(chunk_ids))))
            )
        ).all()
        return {
            chunk_id: RepoDocChunkRecord(
                chunk_id=chunk_id,
                document_id=document_id,
                file_path=file_path,
                title=title,
                heading_path=list(heading_path or []),
                content=content,
            )
            for chunk_id, document_id, file_path, title, heading_path, content in rows
        }

def _snippet(text: str | None, limit: int = 600) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _chunk_metadata(
    chunk: RetrievedChunk,
    *,
    include_scores: bool,
    add_graph_origin: bool = False,
) -> RetrievalMetadata:
    origins: list[CandidateFrom] = []
    if chunk.metadata.get("vector_rank") is not None:
        origins.append(CandidateFrom.VECTOR)
    if chunk.metadata.get("lexical_rank") is not None:
        origins.append(CandidateFrom.LEXICAL)
    if chunk.metadata.get("symbol_rank") is not None:
        origins.append(CandidateFrom.SYMBOL)
    if add_graph_origin:
        origins.append(CandidateFrom.GRAPH)

    if not include_scores:
        return RetrievalMetadata(candidate_from=origins)

    return RetrievalMetadata(
        rerank_score=(
            float(chunk.metadata["rerank_score"])
            if chunk.metadata.get("rerank_score") is not None
            else None
        ),
        candidate_from=origins,
    )


def _code_provenance(node: CodeNode) -> RetrievalProvenance:
    last_changed_at = getattr(node, "last_changed_at", None)
    return RetrievalProvenance(
        node_id=node.id,
        qualified_name=node.qualified_name,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        first_seen_commit=getattr(node, "first_seen_commit", None),
        last_changed_commit=getattr(node, "last_changed_commit", None),
        last_changed_at=_iso_datetime(last_changed_at),
    )


def _related_node_model(node: PivotRelatedNode) -> RetrievalRelatedNode:
    return RetrievalRelatedNode(
        id=node.id,
        name=node.name,
        node_type=node.node_type,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        signature=node.signature,
    )


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
