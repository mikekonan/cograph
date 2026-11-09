"""Phase 7b regression suite against real PostgreSQL + pgvector.

Guards:
    - Pipeline EMBED_REPO_DOCS step writes embeddings to all repo_document_chunks
    - The content_tsv GIN index from migration 0015 exists
    - RepoDocumentEmbedder skip predicate (content_hash + model) works end-to-end:
      second pipeline run with unchanged content embeds 0 chunks

Run:
    COGRAPH_RUN_INTEGRATION=1 uv run pytest backend/integration_tests/test_phase7_regression.py -q

Environment:
    COGRAPH_RUN_INTEGRATION=1             required opt-in
    COGRAPH_INTEGRATION_ADMIN_DSN         postgres admin DSN (default: postgresql://postgres:postgres@127.0.0.1:5432/postgres)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor

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
            host="example.com",
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
            repo_document_embedder_service=RepoDocumentEmbedderService(
                provider, batch_size=64
            ),
        )
        result = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert result.repo_doc_embed_result is not None, (
        "EMBED_REPO_DOCS step must have run"
    )
    assert result.repo_doc_embed_result.embedded_nodes > 0, (
        "at least one chunk must be embedded"
    )

    async with integration_session_manager.session() as session:
        null_count = await session.scalar(
            select(func.count())
            .select_from(RepoDocumentChunk)
            .where(RepoDocumentChunk.embedding.is_(None))
        )

    assert null_count == 0, (
        f"All repo_document_chunks must have embeddings after pipeline; {null_count} are still NULL"
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
            host="example.com",
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
            repo_document_embedder_service=RepoDocumentEmbedderService(
                provider, batch_size=64
            ),
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
