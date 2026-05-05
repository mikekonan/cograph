"""Phase 7a + 7b regression suite against real PostgreSQL + pgvector.

Phase 7a guards:
    - RagRetriever returns code chunks with provenance metadata
    - Bad-store query is swallowed with a WARNING, other stores continue
    - asyncio.CancelledError is never swallowed — re-raised immediately

Phase 7b guards:
    - Pipeline EMBED_REPO_DOCS step writes embeddings to all repo_document_chunks
    - Bank-upload path writes embeddings to all bank_document_chunks
    - BM25 surfaces an exact-name match that the vector path misses (proves content_tsv GIN
      and GENERATED ALWAYS AS STORED columns from migration 0015 are live)
    - RRF promotes an overlap hit (vector+BM25) above a vector-only hit of equal or higher
      cosine similarity
    - RepoDocumentEmbedder skip predicate (content_hash + model) works end-to-end:
      second pipeline run with unchanged content embeds 0 chunks

Run:
    COGRAPH_RUN_INTEGRATION=1 uv run pytest backend/integration_tests/test_phase7_regression.py -q

Environment:
    COGRAPH_RUN_INTEGRATION=1             required opt-in
    COGRAPH_INTEGRATION_ADMIN_DSN         postgres admin DSN (default: postgresql://postgres:postgres@127.0.0.1:5432/postgres)
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from backend.app.banks.indexer import BankDocumentUpsertInput, BankIndexer
from backend.app.llm.bank_document_embedder import BankDocumentEmbedderService
from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.bank import Bank, BankDocumentChunk
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.pipeline.processor import RepoSyncProcessor
from backend.app.rag.retriever import RagRetriever

pytestmark = pytest.mark.integration

_DIMS = 1536  # must match VectorType(1536) in ORM models


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _make_code_node(
    repository_id,
    *,
    qualified_name: str,
    content: str,
    file_path: str = "module.py",
) -> CodeNode:
    return CodeNode(
        repository_id=repository_id,
        file_path=file_path,
        qualified_name=qualified_name,
        node_type=CodeNodeType.FUNCTION,
        name=qualified_name.rsplit(".", 1)[-1],
        language="python",
        start_line=1,
        end_line=10,
        content=content,
        content_hash=_sha256(content),
        callers=[],
        callees=[],
    )


def _checkout_with_docs(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    docs = checkout / "docs"
    docs.mkdir()
    (docs / "overview.md").write_text(
        "# Overview\n\nThis service greets users.\n\n## Usage\n\nCall greet(name).\n",
        encoding="utf-8",
    )
    return checkout


# ---------------------------------------------------------------------------
# Phase 7a — hybrid kNN+BM25 retriever
# ---------------------------------------------------------------------------


async def test_retriever_returns_code_chunks_with_provenance(
    integration_session_manager,
):
    """RagRetriever surfaces code chunks with full provenance metadata.

    Inserts 3 code nodes with real 1536-dim embeddings, queries with the second
    node's own embedding vector, and asserts it appears in results with
    store='code' and all expected provenance fields.
    """
    provider = FakeEmbedProvider(dims=_DIMS)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7a-provenance.git",
            name="phase7a-provenance",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        nodes = []
        for i in range(3):
            content = f"def func_{i}(x: int) -> int:\n    return x + {i}"
            node = _make_code_node(
                repo_id,
                qualified_name=f"module.func_{i}",
                content=content,
                file_path=f"module_{i}.py",
            )
            session.add(node)
            nodes.append(node)
        await session.flush()

        # Generate embeddings and insert code_embedding rows.
        embed_texts = [f"function {n.qualified_name}\n{n.content}" for n in nodes]
        vectors = await provider.embed(embed_texts)
        for node, vec in zip(nodes, vectors):
            session.add(
                CodeEmbedding(
                    code_node_id=node.id,
                    embedding=vec,
                    model=provider.model,
                    content_hash=node.content_hash,
                    neighbor_hash="",
                )
            )
        await session.commit()

        target_node = nodes[1]
        target_vec = vectors[1]  # cosine_sim = 1.0 → always top-1

        retriever = RagRetriever()
        results = await retriever.retrieve(
            session,
            query_text="func_1",
            query_embedding=target_vec,
            repository_id=repo_id,
            stores={"code"},
            top_k=5,
        )

    assert results, "retriever must return at least one result"
    assert all(r.store == "code" for r in results), "all results must be from the code store"
    chunk_ids = {r.chunk_id for r in results}
    assert target_node.id in chunk_ids, "target node must appear when queried with its own vector"

    top = results[0]
    assert "qualified_name" in top.metadata, "qualified_name provenance must be present"
    assert "file_path" in top.metadata, "file_path provenance must be present"
    assert "language" in top.metadata, "language provenance must be present"
    assert "start_line" in top.metadata, "start_line provenance must be present"
    assert "end_line" in top.metadata, "end_line provenance must be present"


async def test_retriever_swallows_bad_store_query_and_continues(
    integration_session_manager,
    monkeypatch,
):
    """A RuntimeError in one store is caught and other stores still proceed.

    The patched _vector_code raises RuntimeError to simulate a broken code-store
    query.  We verify two invariants:
      1. retrieve() returns a list (the exception was caught, not propagated)
      2. the patched code-store method was actually invoked (proves the test
         exercises the C3 try/except path, not just an early-exit short-circuit)

    The WARNING log line is emitted by retriever.py at line ~115 — its presence
    is verified by unit tests that have full control over the logger setup.
    """

    call_log = []

    async def _bad_vector_code(self, session, qvec, repo_id, top_k):
        call_log.append("called")
        raise RuntimeError("simulated code-store failure injected by regression test")

    monkeypatch.setattr(RagRetriever, "_vector_code", _bad_vector_code)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7a-badstore.git",
            name="phase7a-badstore",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id
        await session.commit()

        retriever = RagRetriever()
        results = await retriever.retrieve(
            session,
            query_text="test query",
            query_embedding=[0.1] * _DIMS,
            repository_id=repo_id,
            stores={"code", "repo_docs"},
            top_k=5,
        )

    assert isinstance(results, list), "retrieve() must not raise when a store query fails"
    assert call_log == ["called"], f"_vector_code monkeypatch did not fire: {call_log}"


async def test_retriever_re_raises_cancelled_error(
    integration_session_manager,
    monkeypatch,
):
    """asyncio.CancelledError must propagate — it must never be swallowed by the catch-all.

    Validates the explicit `except asyncio.CancelledError: raise` guard in retrieve().
    """

    async def _raise_cancelled(self, *args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(RagRetriever, "_vector_code", _raise_cancelled)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7a-cancelled.git",
            name="phase7a-cancelled",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id
        await session.commit()

        retriever = RagRetriever()
        with pytest.raises(asyncio.CancelledError):
            await retriever.retrieve(
                session,
                query_text="test",
                query_embedding=[0.1] * _DIMS,
                repository_id=repo_id,
                stores={"code"},
                top_k=5,
            )


# ---------------------------------------------------------------------------
# Phase 7b — embedding writers
# ---------------------------------------------------------------------------


async def test_pipeline_writes_repo_doc_chunk_embeddings(
    integration_session_manager,
    tmp_path,
):
    """Full pipeline run via process_checkout writes embeddings to every repo_document_chunk.

    Proves the EMBED_REPO_DOCS SyncStep is wired after INDEX_REPO_DOCS and that
    RepoDocumentEmbedderService writes non-NULL embeddings to all chunks.
    """
    provider = FakeEmbedProvider(dims=_DIMS)
    checkout = _checkout_with_docs(tmp_path)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7b-pipeline.git",
            name="phase7b-pipeline",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        processor = RepoSyncProcessor(
            code_embedder_service=CodeEmbedderService(provider, batch_size=64),
            repo_document_embedder_service=RepoDocumentEmbedderService(provider, batch_size=64),
        )
        result = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert result.repo_doc_embed_result is not None, "EMBED_REPO_DOCS step must have run"
    assert result.repo_doc_embed_result.embedded_nodes > 0, "at least one chunk must be embedded"

    async with integration_session_manager.session() as session:
        null_count = await session.scalar(
            select(func.count())
            .select_from(RepoDocumentChunk)
            .where(RepoDocumentChunk.embedding.is_(None))
        )

    assert null_count == 0, (
        f"All repo_document_chunks must have embeddings after pipeline; {null_count} are still NULL"
    )


async def test_bank_upload_writes_chunk_embeddings(integration_session_manager):
    """BankDocumentEmbedderService.embed_bank writes embeddings to every bank_document_chunk.

    Proves the bank-upload inline embed path (Phase 7b) populates chunk embeddings.
    """
    provider = FakeEmbedProvider(dims=_DIMS)

    async with integration_session_manager.session() as session:
        owner = User(email="bankowner@example.com", password_hash="hashed")
        bank = Bank(name="Phase7b Bank", owner=owner)
        session.add(bank)
        await session.flush()
        bank_id = bank.id

        indexer = BankIndexer()
        await indexer.upsert_document(
            session=session,
            bank_id=bank_id,
            document=BankDocumentUpsertInput(
                source_key="docs/adr-001.md",
                content=(
                    "# ADR-001\n\nWe chose PostgreSQL.\n\n"
                    "## Context\n\nPrimary data store for persistence.\n\n"
                    "## Decision\n\nPostgreSQL with pgvector for embeddings.\n"
                ),
            ),
        )

        embedder = BankDocumentEmbedderService(provider, batch_size=64)
        embed_result = await embedder.embed_bank(session=session, bank_id=bank_id)
        await session.commit()

    assert embed_result.embedded_nodes > 0, "at least one bank chunk must be embedded"

    async with integration_session_manager.session() as session:
        null_count = await session.scalar(
            select(func.count())
            .select_from(BankDocumentChunk)
            .where(BankDocumentChunk.embedding.is_(None))
        )

    assert null_count == 0, (
        f"All bank_document_chunks must have embeddings after embed_bank; {null_count} are NULL"
    )


# ---------------------------------------------------------------------------
# Phase 7b — BM25 + RRF
# ---------------------------------------------------------------------------


async def test_bm25_gin_index_exists(integration_session_manager):
    """Migration 0015 GIN index on code_nodes.content_tsv must be present.

    This is a schema-level guard: if the index is missing, BM25 queries still work
    but are a seq-scan. A missing index means the migration didn't run.
    """
    async with integration_session_manager.session() as session:
        idx_name = await session.scalar(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'code_nodes' AND indexname = 'idx_code_nodes_tsv'"
            )
        )
    assert idx_name == "idx_code_nodes_tsv", (
        "GIN index idx_code_nodes_tsv missing from code_nodes — did migration 0015 run?"
    )


async def test_bm25_finds_exact_name_that_vector_misses(integration_session_manager):
    """BM25 surfaces a rare-token node even when its vector is distant from the query.

    Setup
    -----
    - Node 'rare'  : qualified_name contains unique token 'zxqpha7m'; its embedding
                     is computed from its own text — NOT the query text.
    - Node 'common': its embedding equals the query embedding (cosine_sim = 1.0).

    Assertions
    ----------
    - Vector-only (top_k=1, empty query_text): returns 'common', NOT 'rare'.
    - Hybrid (query_text='zxqpha7m'): returns 'rare' (BM25 found it); its metadata
      includes bm25_rank, confirming the BM25 path contributed.
    """
    provider = FakeEmbedProvider(dims=_DIMS)

    rare_qname = "module.zxqpha7m"
    rare_content = "def zxqpha7m(): return None"
    common_qname = "module.common_helper"
    common_content = "def common_helper(): return True"

    rare_text = f"function {rare_qname}\n{rare_content}"
    common_text = f"function {common_qname}\n{common_content}"
    rare_vec, common_vec = await provider.embed([rare_text, common_text])

    # query_embedding equals common_vec → cosine_sim(query, common) = 1.0
    query_embedding = common_vec

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7b-bm25.git",
            name="phase7b-bm25",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        rare_node = _make_code_node(repo_id, qualified_name=rare_qname, content=rare_content)
        common_node = _make_code_node(repo_id, qualified_name=common_qname, content=common_content)
        session.add_all([rare_node, common_node])
        await session.flush()

        session.add(
            CodeEmbedding(
                code_node_id=rare_node.id,
                embedding=rare_vec,
                model=provider.model,
                content_hash=rare_node.content_hash,
                neighbor_hash="",
            )
        )
        session.add(
            CodeEmbedding(
                code_node_id=common_node.id,
                embedding=common_vec,
                model=provider.model,
                content_hash=common_node.content_hash,
                neighbor_hash="",
            )
        )
        await session.commit()

        rare_id = rare_node.id
        common_id = common_node.id

        retriever = RagRetriever()

        # Vector-only: top_k=1, empty query_text disables BM25.
        vector_only = await retriever.retrieve(
            session,
            query_text="",
            query_embedding=query_embedding,
            repository_id=repo_id,
            stores={"code"},
            top_k=1,
        )

        # Hybrid: BM25 should find 'rare' via the 'zxqpha7m' token.
        hybrid = await retriever.retrieve(
            session,
            query_text="zxqpha7m",
            query_embedding=query_embedding,
            repository_id=repo_id,
            stores={"code"},
            top_k=5,
        )

    assert len(vector_only) == 1, "vector-only top_k=1 must return exactly one result"
    assert vector_only[0].chunk_id == common_id, (
        "vector-only top-1 must be the node whose embedding matches the query vector"
    )

    hybrid_ids = {r.chunk_id for r in hybrid}
    assert rare_id in hybrid_ids, (
        "hybrid retrieval must surface the rare-token node via BM25 "
        "(content_tsv GENERATED ALWAYS AS STORED + GIN index must be live)"
    )

    rare_result = next(r for r in hybrid if r.chunk_id == rare_id)
    assert rare_result.metadata.get("bm25_rank") is not None, (
        "rare-token result must carry a bm25_rank — BM25 must have contributed to its score"
    )


async def test_rrf_boosts_overlap_hits(integration_session_manager):
    """RRF ranks a hit found by BOTH vector and BM25 above a hit found by vector only.

    Setup
    -----
    - Node 'overlap': content contains unique BM25 token 'zxqrrf9'; its embedding
                      is derived from its own text (vector rank 2 vs query).
    - Node 'vector_only': embedding equals query embedding (vector rank 1); its
                          content does NOT contain 'zxqrrf9' (no BM25 match).

    Without RRF, 'vector_only' (rank 1) would beat 'overlap' (rank 2).
    With RRF (k=60):
      overlap    = 1/(60+2) + 1/(60+1) ≈ 0.0325   [vector rank 2 + BM25 rank 1]
      vector_only = 1/(60+1)            ≈ 0.0164   [vector rank 1 only]

    So 'overlap' must rank above 'vector_only' in the final results.
    """
    provider = FakeEmbedProvider(dims=_DIMS)

    overlap_qname = "module.overlap_func"
    overlap_content = "def overlap_func(): pass  # zxqrrf9"
    vo_qname = "module.vector_only_func"
    vo_content = "def vector_only_func(): return 42"

    overlap_text = f"function {overlap_qname}\n{overlap_content}"
    vo_text = f"function {vo_qname}\n{vo_content}"
    overlap_vec, vo_vec = await provider.embed([overlap_text, vo_text])

    # query_embedding equals vo_vec → cosine_sim(query, vo) = 1.0 → vo is vector rank 1
    query_embedding = vo_vec

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7b-rrf.git",
            name="phase7b-rrf",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        overlap_node = _make_code_node(repo_id, qualified_name=overlap_qname, content=overlap_content)
        vo_node = _make_code_node(repo_id, qualified_name=vo_qname, content=vo_content)
        session.add_all([overlap_node, vo_node])
        await session.flush()

        session.add(
            CodeEmbedding(
                code_node_id=overlap_node.id,
                embedding=overlap_vec,
                model=provider.model,
                content_hash=overlap_node.content_hash,
                neighbor_hash="",
            )
        )
        session.add(
            CodeEmbedding(
                code_node_id=vo_node.id,
                embedding=vo_vec,
                model=provider.model,
                content_hash=vo_node.content_hash,
                neighbor_hash="",
            )
        )
        await session.commit()

        overlap_id = overlap_node.id
        vo_id = vo_node.id

        retriever = RagRetriever()
        results = await retriever.retrieve(
            session,
            query_text="zxqrrf9",
            query_embedding=query_embedding,
            repository_id=repo_id,
            stores={"code"},
            top_k=5,
        )

    result_ids = [r.chunk_id for r in results]
    assert overlap_id in result_ids, "overlap node must appear in results"
    assert vo_id in result_ids, "vector-only node must appear in results"

    overlap_pos = result_ids.index(overlap_id)
    vo_pos = result_ids.index(vo_id)
    assert overlap_pos < vo_pos, (
        f"RRF must rank the overlap hit (pos={overlap_pos}) above the vector-only hit (pos={vo_pos}). "
        "The overlap node is vector rank 2 + BM25 rank 1; vector-only is vector rank 1 + no BM25. "
        "With k=60: overlap RRF ≈ 0.0325 > vector_only RRF ≈ 0.0164."
    )

    overlap_result = results[overlap_pos]
    assert overlap_result.metadata.get("bm25_rank") == 1, "overlap node must be BM25 rank 1"

    vo_result = results[vo_pos]
    assert vo_result.metadata.get("bm25_rank") is None, "vector-only node must have no BM25 rank"
    assert vo_result.metadata.get("vector_rank") == 1, "vector-only node must be vector rank 1"


# ---------------------------------------------------------------------------
# Phase 7b — writer skip predicate (content_hash + model)
# ---------------------------------------------------------------------------


async def test_phase7b_writers_use_skip_predicate(
    integration_session_manager,
    tmp_path,
):
    """RepoDocumentEmbedder skip predicate prevents re-embedding unchanged chunks.

    First run: all chunks get embeddings (embedded_nodes > 0).
    Second run with the same checkout: 0 nodes embedded, all skipped.

    Validates the content_hash + model skip predicate added by the Phase 7b migration
    (migration 0016 adds content_hash to both chunk tables).
    """
    provider = FakeEmbedProvider(dims=_DIMS)
    checkout = _checkout_with_docs(tmp_path)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7b-skip.git",
            name="phase7b-skip",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        processor = RepoSyncProcessor(
            code_embedder_service=CodeEmbedderService(provider, batch_size=64),
            repo_document_embedder_service=RepoDocumentEmbedderService(provider, batch_size=64),
        )

        first = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert first.repo_doc_embed_result is not None
    first_embedded = first.repo_doc_embed_result.embedded_nodes
    assert first_embedded > 0, "first run must embed at least one chunk"

    async with integration_session_manager.session() as session:
        second = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert second.repo_doc_embed_result is not None
    assert second.repo_doc_embed_result.embedded_nodes == 0, (
        "second run with unchanged content must embed 0 chunks (skip predicate must fire)"
    )
    assert second.repo_doc_embed_result.skipped_nodes == first_embedded, (
        "second run must skip exactly as many chunks as the first run embedded"
    )
