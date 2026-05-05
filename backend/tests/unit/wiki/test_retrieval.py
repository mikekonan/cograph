"""Tests for the wiki retrieval adapter (`WikiRetrievalService`)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.rag.pivot import PivotNode, PivotRelatedNode
from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.retrieval import WikiRetrievalService

pytestmark = pytest.mark.asyncio


class _FakeHybrid:
    """Records calls and returns canned RetrievedChunk lists per store."""

    def __init__(
        self, *, code: list[RetrievedChunk], docs: list[RetrievedChunk]
    ) -> None:
        self._code = code
        self._docs = docs
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        query_embedding: list[float],
        repository_id: UUID | None = None,
        top_k: int = 10,
        stores: set[str] | None = None,
        **_: Any,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_text": query_text,
                "embedding_dim": len(query_embedding),
                "repository_id": repository_id,
                "top_k": top_k,
                "stores": set(stores or set()),
            }
        )
        if stores == {"code"}:
            return list(self._code)
        if stores == {"repo_docs"}:
            return list(self._docs)
        return []


class _FakePivot:
    def __init__(self, *, expansions: dict[UUID, PivotNode]) -> None:
        self._expansions = expansions
        self.calls: list[list[UUID]] = []

    async def expand(
        self, *, session: AsyncSession, repository_id: UUID, node_ids: list[UUID]
    ) -> dict[UUID, PivotNode]:
        self.calls.append(list(node_ids))
        return {
            nid: self._expansions[nid] for nid in node_ids if nid in self._expansions
        }


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-retrieval",
        name="wiki-llm-retrieval",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node_with_summary(
    session: AsyncSession,
    *,
    repo_id: UUID,
    qn: str,
    summary_text: str,
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path="src/pipeline.py",
        qualified_name=qn,
        node_type=CodeNodeType.FUNCTION,
        name=qn.rsplit(".", 1)[-1],
        language="python",
        start_line=10,
        end_line=42,
        content="def fn(): pass\n",
        content_hash="x" * 64,
    )
    session.add(node)
    await session.flush()
    session.add(
        CodeNodeSummary(
            code_node_id=node.id,
            repository_id=repo_id,
            summary=summary_text,
            importance=0.9,
            content_hash="y" * 64,
            neighbor_hash="z" * 64,
            model="fake-summary-v1",
        )
    )
    await session.flush()
    return node


async def test_for_overview_pulls_summaries_and_count(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.pipeline.run",
        summary_text="Top-level orchestrator.",
    )
    await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.pipeline.helper",
        summary_text="Helper.",
    )

    service = WikiRetrievalService(
        hybrid=_FakeHybrid(code=[], docs=[]),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
    )
    bundle = await service.for_overview(
        session=db_session, repository_id=repo.id, top_n=5
    )
    assert bundle.code_node_count == 2
    qns = sorted(s.qualified_name for s in bundle.top_summaries)
    assert qns == ["src.pipeline.helper", "src.pipeline.run"]


async def test_for_page_packs_chunks_summaries_and_neighbors(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    main_node = await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.pipeline.run",
        summary_text="Top-level orchestrator.",
    )

    code_hits = [
        RetrievedChunk(
            store="code",
            chunk_id=main_node.id,
            content="def run():\n    pass\n",
            score=0.9,
            metadata={
                "qualified_name": "src.pipeline.run",
                "file_path": "src/pipeline.py",
                "language": "python",
                "start_line": 10,
                "end_line": 42,
            },
        ),
    ]
    doc_chunk_id = uuid4()
    doc_hits = [
        RetrievedChunk(
            store="repo_docs",
            chunk_id=doc_chunk_id,
            content="The pipeline runs in 5 stages.",
            score=0.7,
            metadata={
                "file_path": "docs/architecture.md",
                "title": "Architecture",
                "chunk_index": 0,
                "heading_path": ["Architecture", "Overview"],
            },
        ),
    ]
    pivot_neighbor = PivotNode(
        id=main_node.id,
        name="src.pipeline.run",
        node_type=CodeNodeType.FUNCTION,
        language="python",
        file_path="src/pipeline.py",
        start_line=10,
        end_line=42,
        signature=None,
        callers=[
            PivotRelatedNode(
                id=uuid4(),
                name="src.cli.main",
                node_type=CodeNodeType.FUNCTION,
                file_path="src/cli.py",
                start_line=5,
                end_line=10,
                signature=None,
            )
        ],
        callees=[],
        parent=None,
    )

    fake_hybrid = _FakeHybrid(code=code_hits, docs=doc_hits)
    fake_pivot = _FakePivot(expansions={main_node.id: pivot_neighbor})
    service = WikiRetrievalService(
        hybrid=fake_hybrid,  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
        pivot=fake_pivot,  # type: ignore[arg-type]
    )

    bundle = await service.for_page(
        session=db_session,
        repository_id=repo.id,
        purpose="How is the pipeline organized?",
        sources_hint=["src/pipeline.py"],
        code_top_k=5,
        docs_top_k=3,
        graph_pivot_top_k=2,
    )

    assert len(bundle.code_chunks) == 1
    code_chunk = bundle.code_chunks[0]
    assert code_chunk.qualified_name == "src.pipeline.run"
    assert code_chunk.summary == "Top-level orchestrator."
    assert code_chunk.code_node_id == main_node.id

    assert len(bundle.doc_chunks) == 1
    doc_chunk = bundle.doc_chunks[0]
    assert doc_chunk.file_path == "docs/architecture.md"
    assert doc_chunk.heading_path == ["Architecture", "Overview"]
    assert doc_chunk.chunk_id == doc_chunk_id

    assert len(bundle.graph_neighbors) == 1
    neighbor = bundle.graph_neighbors[0]
    assert neighbor.qualified_name == "src.cli.main"
    assert neighbor.role == "caller"

    # Hybrid is called once per store.
    stores_called = [c["stores"] for c in fake_hybrid.calls]
    assert {"code"} in stores_called
    assert {"repo_docs"} in stores_called
    assert len(fake_hybrid.calls) == 2

    # Pivot expansion limited by graph_pivot_top_k.
    assert fake_pivot.calls == [[main_node.id]]


async def test_for_page_handles_empty_retrieval(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    fake_hybrid = _FakeHybrid(code=[], docs=[])
    service = WikiRetrievalService(
        hybrid=fake_hybrid,  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
        pivot=_FakePivot(expansions={}),  # type: ignore[arg-type]
    )
    bundle = await service.for_page(
        session=db_session,
        repository_id=repo.id,
        purpose="anything",
        sources_hint=[],
    )
    assert bundle.code_chunks == []
    assert bundle.doc_chunks == []
    assert bundle.graph_neighbors == []


async def test_for_page_applies_domain_rerank_when_concepts_present(
    db_session: AsyncSession,
) -> None:
    """T6: a domain concept match should reorder close-baseline hits."""
    from backend.app.wiki.schemas import (
        BusinessContextConfidence,
        DomainConcept,
    )

    repo = await _make_repo(db_session)
    account_node = await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.account.credit",
        summary_text="Credits an account.",
    )
    util_node = await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.util.helper",
        summary_text="Generic helper.",
    )

    # `helper` has a higher raw score; without rerank it would lead.
    code_hits = [
        RetrievedChunk(
            store="code",
            chunk_id=util_node.id,
            content="def helper(): pass",
            score=0.061,
            metadata={
                "qualified_name": "src.util.helper",
                "file_path": "src/util.py",
                "language": "python",
                "start_line": 1,
                "end_line": 5,
            },
        ),
        RetrievedChunk(
            store="code",
            chunk_id=account_node.id,
            content="def credit_account(): pass",
            score=0.060,
            metadata={
                "qualified_name": "src.account.credit",
                "file_path": "src/account.py",
                "language": "python",
                "start_line": 1,
                "end_line": 5,
            },
        ),
    ]

    service = WikiRetrievalService(
        hybrid=_FakeHybrid(code=code_hits, docs=[]),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
        pivot=_FakePivot(expansions={}),  # type: ignore[arg-type]
    )
    bundle = await service.for_page(
        session=db_session,
        repository_id=repo.id,
        purpose="how does account credit work?",
        sources_hint=[],
        domain_concepts=[
            DomainConcept(
                name="Account",
                definition="Customer account.",
                importance=0.9,
            )
        ],
        business_confidence=BusinessContextConfidence.HIGH,
    )
    # Account-mentioning hit is now first (concept boost outweighs the
    # tiny RRF lead).
    qns = [c.qualified_name for c in bundle.code_chunks]
    assert qns[0] == "src.account.credit"


async def test_for_page_rerank_noop_when_no_concepts(
    db_session: AsyncSession,
) -> None:
    """Empty domain_concepts → original RRF order is preserved."""
    repo = await _make_repo(db_session)
    util_node = await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.util.helper",
        summary_text="Helper.",
    )
    account_node = await _add_node_with_summary(
        db_session,
        repo_id=repo.id,
        qn="src.account.credit",
        summary_text="Credit.",
    )
    code_hits = [
        RetrievedChunk(
            store="code",
            chunk_id=util_node.id,
            content="def helper(): pass",
            score=0.061,
            metadata={
                "qualified_name": "src.util.helper",
                "file_path": "src/util.py",
                "language": "python",
                "start_line": 1,
                "end_line": 5,
            },
        ),
        RetrievedChunk(
            store="code",
            chunk_id=account_node.id,
            content="def credit_account(): pass",
            score=0.060,
            metadata={
                "qualified_name": "src.account.credit",
                "file_path": "src/account.py",
                "language": "python",
                "start_line": 1,
                "end_line": 5,
            },
        ),
    ]
    service = WikiRetrievalService(
        hybrid=_FakeHybrid(code=code_hits, docs=[]),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
        pivot=_FakePivot(expansions={}),  # type: ignore[arg-type]
    )
    bundle = await service.for_page(
        session=db_session,
        repository_id=repo.id,
        purpose="anything",
        sources_hint=[],
        domain_concepts=[],
    )
    qns = [c.qualified_name for c in bundle.code_chunks]
    assert qns == ["src.util.helper", "src.account.credit"]
