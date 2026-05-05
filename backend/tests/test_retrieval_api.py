from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.app.api.retrieval import get_hybrid_retriever, get_query_embed_provider
from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk, BankFact
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import (
    BankDocumentSourceKind,
    CodeNodeType,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repo_document_chunk_mention import RepoDocumentChunkMention
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.rag.retriever import RetrievedChunk


class _StubEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


class _StubRetriever:
    def __init__(self, hits: list[RetrievedChunk]):
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    async def retrieve(self, session, **kwargs):
        del session
        self.calls.append(kwargs)
        return list(self.hits)


@pytest.mark.asyncio
async def test_retrieve_rejects_top_k_gt_100(client):
    response = await client.post(
        "/api/retrieve",
        json={"query": "repo", "bank_ids": [str(uuid4())], "top_k": 101},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["field_errors"][0]["field"] == "top_k"


@pytest.mark.asyncio
async def test_retrieve_rejects_unknown_layer_name(client):
    response = await client.post(
        "/api/retrieve",
        json={"query": "repo", "bank_ids": [str(uuid4())], "stores": ["made_up"]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["field_errors"][0]["field"] == "stores.0"


@pytest.mark.asyncio
async def test_retrieve_hides_admin_only_repo_from_anonymous(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.post(
        "/api/retrieve",
        json={"query": "repo", "repository_id": str(repository.id)},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_retrieve_returns_composite_results_and_graph_context(app, client, db_session):
    window_start = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    window_end = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.USER)
    bank = Bank(name="Runbooks", description="Ops", owner=owner)
    db_session.add_all([repository, owner, bank])
    await db_session.flush()

    helper_id = uuid4()
    error_id = uuid4()
    helper_node = CodeNode(
        id=helper_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.helper",
        symbol_key="svc.helper",
        node_type=CodeNodeType.FUNCTION,
        name="helper",
        language="python",
        start_line=10,
        end_line=12,
        start_byte=None,
        end_byte=None,
        content="def helper() -> None:\n    return None",
        signature="def helper() -> None",
        doc_comment=None,
        summary=None,
        role=None,
        parent_id=None,
        callers=[str(error_id)],
        callees=[],
        node_metadata={},
        content_hash="helper-hash",
    )
    error_node = CodeNode(
        id=error_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.raise_repo_not_ready",
        symbol_key="svc.raise_repo_not_ready",
        node_type=CodeNodeType.FUNCTION,
        name="raise_repo_not_ready",
        language="python",
        start_line=1,
        end_line=8,
        start_byte=None,
        end_byte=None,
        content="def raise_repo_not_ready() -> None:\n    raise RuntimeError('E_REPO_NOT_READY')",
        signature="def raise_repo_not_ready() -> None",
        doc_comment=None,
        summary=None,
        role=None,
        parent_id=None,
        callers=[],
        callees=[str(helper_id)],
        node_metadata={},
        content_hash="error-hash",
        first_seen_commit="1111111111111111111111111111111111111111",
        last_changed_commit="2222222222222222222222222222222222222222",
        last_changed_at=datetime(2026, 4, 18, 9, 30, tzinfo=UTC),
    )
    db_session.add_all([helper_node, error_node])
    db_session.add(
        CodeNodeSummary(
            code_node_id=error_node.id,
            repository_id=repository.id,
            summary="Raises the repo-not-ready guardrail.",
            importance=0.8,
            content_hash="summary-hash",
            neighbor_hash="neighbor-hash",
            model="gpt-4o-mini",
        )
    )

    repo_document = RepoDocument(
        repository_id=repository.id,
        file_path="docs/errors.md",
        title="Errors",
        content="# Errors\n\nE_REPO_NOT_READY happens while indexing is incomplete.",
        content_hash="repo-doc-hash",
        bytes=64,
    )
    db_session.add(repo_document)
    await db_session.flush()
    repo_chunk = RepoDocumentChunk(
        document_id=repo_document.id,
        chunk_index=0,
        heading_path=["Errors"],
        content="E_REPO_NOT_READY happens while indexing is incomplete.",
        content_hash="repo-chunk-hash",
        mentions=["svc.raise_repo_not_ready"],
    )
    db_session.add(repo_chunk)
    await db_session.flush()
    db_session.add(
        RepoDocumentChunkMention(chunk_id=repo_chunk.id, code_node_id=error_node.id)
    )

    bank_document = BankDocument(
        bank_id=bank.id,
        title="Ops guide",
        source_kind=BankDocumentSourceKind.UPLOAD,
        source_key="runbooks/repo.md",
        external_id=None,
        content="# Ops\n\nIf the repo is not ready, retry later.",
        content_hash="bank-doc-hash",
        bytes=48,
        document_metadata={},
    )
    db_session.add(bank_document)
    await db_session.flush()
    bank_chunk = BankDocumentChunk(
        document_id=bank_document.id,
        chunk_index=0,
        heading_path=["Ops"],
        content="If the repo is not ready, retry later.",
        content_hash="bank-chunk-hash",
    )
    db_session.add(bank_chunk)
    await db_session.flush()
    bank_fact = BankFact(
        bank_id=bank.id,
        document_id=bank_document.id,
        chunk_id=bank_chunk.id,
        statement="Repository actions should be retried after indexing finishes.",
        source_excerpt="Retry later if the repo is not ready.",
        heading_path=["Ops"],
        content_hash="bank-fact-hash",
        extraction_model="gpt-4o-mini",
        model="fake-embed-v1",
    )
    db_session.add(bank_fact)
    await db_session.commit()

    retriever = _StubRetriever(
        [
            RetrievedChunk(
                store="code",
                chunk_id=error_node.id,
                content=error_node.content,
                score=0.91,
                metadata={
                    "qualified_name": error_node.qualified_name,
                    "file_path": error_node.file_path,
                    "start_line": error_node.start_line,
                    "end_line": error_node.end_line,
                    "vector_rank": 1,
                    "lexical_rank": 1,
                    "symbol_rank": 1,
                },
            ),
            RetrievedChunk(
                store="repo_docs",
                chunk_id=repo_chunk.id,
                content=repo_chunk.content,
                score=0.4,
                metadata={"lexical_rank": 1},
            ),
            RetrievedChunk(
                store="bank_facts",
                chunk_id=bank_fact.id,
                content=bank_fact.statement,
                score=0.18,
                metadata={"lexical_rank": 1},
            ),
            RetrievedChunk(
                store="banks",
                chunk_id=bank_chunk.id,
                content=bank_chunk.content,
                score=0.2,
                metadata={"vector_rank": 1},
            ),
        ]
    )
    app.dependency_overrides[get_query_embed_provider] = lambda: _StubEmbedProvider()
    app.dependency_overrides[get_hybrid_retriever] = lambda: retriever

    try:
        response = await client.post(
            "/api/retrieve",
            json={
                "query": "repo not ready",
                "repository_id": str(repository.id),
                "bank_ids": [str(bank.id)],
                "stores": ["code", "ast", "ast_summary", "repo_doc", "bank_fact", "bank"],
                "top_k": 5,
                "since": window_start.isoformat().replace("+00:00", "Z"),
                "until": window_end.isoformat().replace("+00:00", "Z"),
                "include": {"chunks": True, "graph": True, "scores": True},
            },
        )
    finally:
        app.dependency_overrides.pop(get_query_embed_provider, None)
        app.dependency_overrides.pop(get_hybrid_retriever, None)

    assert response.status_code == 200
    payload = response.json()
    assert [item["layer"] for item in payload["results"]] == [
        "code",
        "ast",
        "ast_summary",
        "repo_doc",
        "bank_fact",
        "bank",
    ]
    assert payload["results"][0]["related_repo_doc_chunks"][0]["file_path"] == "docs/errors.md"
    assert payload["results"][0]["provenance"]["first_seen_commit"] == "1111111111111111111111111111111111111111"
    assert payload["results"][0]["provenance"]["last_changed_commit"] == "2222222222222222222222222222222222222222"
    assert payload["results"][0]["provenance"]["last_changed_at"] == "2026-04-18T09:30:00+00:00"
    assert payload["nodes"][str(error_node.id)]["callees"][0]["name"] == "helper"
    assert payload["nodes"][str(error_node.id)]["summary"] == "Raises the repo-not-ready guardrail."
    assert retriever.calls[0]["stores"] == {"code", "repo_docs", "bank_facts", "banks"}
    assert retriever.calls[0]["since"] == window_start
    assert retriever.calls[0]["until"] == window_end


@pytest.mark.asyncio
async def test_retrieve_rejects_inverted_temporal_window(client):
    response = await client.post(
        "/api/retrieve",
        json={
            "query": "repo",
            "bank_ids": [str(uuid4())],
            "since": "2026-04-21T12:00:00Z",
            "until": "2026-04-01T00:00:00Z",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_retrieve_returns_empty_for_repo_not_ready(app, client, db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.commit()

    retriever = _StubRetriever([])
    app.dependency_overrides[get_query_embed_provider] = lambda: _StubEmbedProvider()
    app.dependency_overrides[get_hybrid_retriever] = lambda: retriever

    try:
        response = await client.post(
            "/api/retrieve",
            json={
                "query": "repo not ready",
                "repository_id": str(repository.id),
                "stores": ["code"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_query_embed_provider, None)
        app.dependency_overrides.pop(get_hybrid_retriever, None)

    assert response.status_code == 200
    assert response.json() == {"results": [], "nodes": {}}
    assert retriever.calls == []
