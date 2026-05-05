from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.models.enums import CodeNodeType
from backend.app.rag.context_builder import (
    CandidateFrom,
    ContextBuilder,
    LinkedRepoDocumentChunk,
    RepoDocChunkRecord,
    RetrievalLayer,
)
from backend.app.rag.pivot import PivotNode, PivotRelatedNode
from backend.app.rag.retriever import RetrievedChunk


class _StubGraphPivot:
    def __init__(self, nodes):
        self.nodes = nodes
        self.calls = 0

    async def expand(self, *, session, repository_id, node_ids):
        del session, repository_id, node_ids
        self.calls += 1
        return self.nodes


@pytest.mark.asyncio
async def test_build_expands_code_hit_into_composite_layers_and_graph():
    node_id = uuid4()
    repo_chunk_id = uuid4()
    bank_chunk_id = uuid4()
    related_chunk = LinkedRepoDocumentChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_path="docs/errors.md",
        title="Errors",
        heading_path=["Errors"],
        snippet="Repo not ready guidance.",
    )
    pivot = _StubGraphPivot(
        {
            node_id: PivotNode(
                id=node_id,
                name="raise_repo_not_ready",
                node_type=CodeNodeType.FUNCTION,
                language="python",
                file_path="svc.py",
                start_line=1,
                end_line=8,
                signature="def raise_repo_not_ready() -> None",
                callers=[],
                callees=[
                    PivotRelatedNode(
                        id=uuid4(),
                        name="helper",
                        node_type=CodeNodeType.FUNCTION,
                        file_path="svc.py",
                        start_line=10,
                        end_line=12,
                        signature="def helper() -> None",
                    )
                ],
                parent=None,
            )
        }
    )
    builder = ContextBuilder(graph_pivot=pivot)
    builder._load_code_nodes = AsyncMock(  # type: ignore[method-assign]
        return_value={
            node_id: SimpleNamespace(
                id=node_id,
                name="raise_repo_not_ready",
                qualified_name="svc.raise_repo_not_ready",
                file_path="svc.py",
                start_line=1,
                end_line=8,
                content="raise RuntimeError('E_REPO_NOT_READY')",
                signature="def raise_repo_not_ready() -> None",
                first_seen_commit="1111111111111111111111111111111111111111",
                last_changed_commit="2222222222222222222222222222222222222222",
                last_changed_at=datetime(2026, 4, 18, 9, 30, tzinfo=UTC),
            )
        }
    )
    builder._load_node_summaries = AsyncMock(return_value={node_id: "Raises the repo-not-ready guardrail."})  # type: ignore[method-assign]
    builder._load_linked_repo_doc_chunks = AsyncMock(return_value={node_id: [related_chunk]})  # type: ignore[method-assign]
    builder._load_repo_doc_chunks = AsyncMock(  # type: ignore[method-assign]
        return_value={
            repo_chunk_id: RepoDocChunkRecord(
                chunk_id=repo_chunk_id,
                document_id=uuid4(),
                file_path="docs/errors.md",
                title="Errors",
                heading_path=["Errors"],
                content="E_REPO_NOT_READY means the repository has not finished indexing.",
            )
        }
    )
    builder._load_bank_chunks = AsyncMock(  # type: ignore[method-assign]
        return_value={
            bank_chunk_id: SimpleNamespace(
                chunk_id=bank_chunk_id,
                document_id=uuid4(),
                bank_id=uuid4(),
                bank_name="Runbooks",
                title="Ops guide",
                heading_path=["Incidents"],
                content="Use the runbook when the repo is not ready.",
            )
        }
    )

    response = await builder.build(
        AsyncMock(),
        chunks=[
            RetrievedChunk(
                store="code",
                chunk_id=node_id,
                content="ignored",
                score=0.73,
                metadata={
                    "vector_rank": 1,
                    "lexical_rank": 1,
                    "symbol_rank": 1,
                    "rerank_score": 0.91,
                },
            ),
            RetrievedChunk(
                store="repo_docs",
                chunk_id=repo_chunk_id,
                content="ignored",
                score=0.44,
                metadata={"lexical_rank": 1},
            ),
            RetrievedChunk(
                store="banks",
                chunk_id=bank_chunk_id,
                content="ignored",
                score=0.22,
                metadata={"vector_rank": 1},
            ),
        ],
        requested_layers=set(RetrievalLayer),
        repository_id=uuid4(),
        include_chunks=True,
        include_graph=True,
        include_scores=True,
    )

    assert [item.layer for item in response.results] == [
        RetrievalLayer.CODE,
        RetrievalLayer.AST,
        RetrievalLayer.AST_SUMMARY,
        RetrievalLayer.REPO_DOC,
        RetrievalLayer.BANK,
    ]
    assert response.results[0].related_repo_doc_chunks == [related_chunk]
    assert set(response.results[0].metadata.candidate_from) == {
        CandidateFrom.VECTOR,
        CandidateFrom.LEXICAL,
        CandidateFrom.SYMBOL,
    }
    assert response.results[1].metadata.candidate_from[-1] is CandidateFrom.GRAPH
    assert response.results[2].snippet == "Raises the repo-not-ready guardrail."
    assert response.results[0].provenance.first_seen_commit == "1111111111111111111111111111111111111111"
    assert response.results[0].provenance.last_changed_commit == "2222222222222222222222222222222222222222"
    assert response.results[0].provenance.last_changed_at == "2026-04-18T09:30:00+00:00"
    assert response.nodes[str(node_id)].callees[0].name == "helper"
    assert response.nodes[str(node_id)].summary == "Raises the repo-not-ready guardrail."


@pytest.mark.asyncio
async def test_build_respects_include_flags_and_skips_missing_summary():
    node_id = uuid4()
    builder = ContextBuilder(graph_pivot=_StubGraphPivot({}))
    builder._load_code_nodes = AsyncMock(  # type: ignore[method-assign]
        return_value={
            node_id: SimpleNamespace(
                id=node_id,
                name="lookup",
                qualified_name="svc.lookup",
                file_path="svc.py",
                start_line=1,
                end_line=4,
                content="def lookup() -> None:\n    return None",
                signature="def lookup() -> None",
            )
        }
    )
    builder._load_node_summaries = AsyncMock(return_value={})  # type: ignore[method-assign]
    builder._load_linked_repo_doc_chunks = AsyncMock(return_value={})  # type: ignore[method-assign]
    builder._load_repo_doc_chunks = AsyncMock(return_value={})  # type: ignore[method-assign]
    builder._load_bank_chunks = AsyncMock(return_value={})  # type: ignore[method-assign]

    response = await builder.build(
        AsyncMock(),
        chunks=[
            RetrievedChunk(
                store="code",
                chunk_id=node_id,
                content="ignored",
                score=0.5,
                metadata={"vector_rank": 1},
            )
        ],
        requested_layers={RetrievalLayer.CODE, RetrievalLayer.AST_SUMMARY},
        repository_id=uuid4(),
        include_chunks=False,
        include_graph=False,
        include_scores=False,
    )

    assert len(response.results) == 1
    assert response.results[0].layer is RetrievalLayer.CODE
    assert response.results[0].score is None
    assert response.results[0].related_repo_doc_chunks == []
    assert response.nodes == {}
