from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.api.retrieval import get_query_embed_provider
from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import BankDocumentSourceKind, CodeNodeType, RepositoryStatus, SyncSchedule, UserRole
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repo_document_chunk_mention import RepoDocumentChunkMention
from backend.app.models.repository import Repository
from backend.app.models.user import User


class _StubEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return [_vector(1.0, 0.0)]


def _vector(*values: float) -> list[float]:
    head = list(values)
    return head + [0.0] * (1536 - len(head))


@pytest.mark.asyncio
async def test_live_postgres_retrieve_endpoint_returns_layered_composite(
    integration_app,
    integration_client,
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.READY,
            sync_schedule=SyncSchedule.MANUAL,
        )
        owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.USER)
        bank = Bank(name="Runbooks", description="Ops", owner=owner)
        session.add_all([repository, owner, bank])
        await session.flush()

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
        )
        session.add_all([helper_node, error_node])
        await session.flush()
        session.add(
            CodeEmbedding(
                code_node_id=error_node.id,
                embedding=_vector(1.0),
                model="fake-embed-v1",
                content_hash=error_node.content_hash,
                neighbor_hash="neighbor-hash",
            )
        )
        session.add(
            CodeEmbedding(
                code_node_id=helper_node.id,
                embedding=_vector(0.2),
                model="fake-embed-v1",
                content_hash=helper_node.content_hash,
                neighbor_hash="neighbor-hash-helper",
            )
        )
        session.add(
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
        session.add(repo_document)
        await session.flush()
        repo_chunk = RepoDocumentChunk(
            document_id=repo_document.id,
            chunk_index=0,
            heading_path=["Errors"],
            content="E_REPO_NOT_READY happens while indexing is incomplete.",
            content_hash="repo-chunk-hash",
            mentions=["svc.raise_repo_not_ready"],
            embedding=_vector(1.0),
            model="fake-embed-v1",
        )
        session.add(repo_chunk)
        await session.flush()
        session.add(
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
        session.add(bank_document)
        await session.flush()
        bank_chunk = BankDocumentChunk(
            document_id=bank_document.id,
            chunk_index=0,
            heading_path=["Ops"],
            content="If the repo is not ready, retry later.",
            content_hash="bank-chunk-hash",
            embedding=_vector(1.0),
            model="fake-embed-v1",
        )
        session.add(bank_chunk)
        await session.commit()

    integration_app.dependency_overrides[get_query_embed_provider] = lambda: _StubEmbedProvider()
    try:
        response = await integration_client.post(
            "/api/retrieve",
            json={
                "query": "E_REPO_NOT_READY",
                "repository_id": str(repository.id),
                "bank_ids": [str(bank.id)],
                "stores": ["code", "ast", "ast_summary", "repo_doc", "bank"],
                "top_k": 5,
                "include": {"chunks": True, "graph": True, "scores": True},
            },
        )
    finally:
        integration_app.dependency_overrides.pop(get_query_embed_provider, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["layer"] == "code"
    assert payload["results"][0]["provenance"]["qualified_name"] == "svc.raise_repo_not_ready"
    assert {item["layer"] for item in payload["results"]} >= {
        "code",
        "ast",
        "ast_summary",
        "repo_doc",
        "bank",
    }
    assert payload["nodes"][str(error_id)]["callees"][0]["name"] == "helper"


@pytest.mark.asyncio
async def test_live_postgres_retrieve_endpoint_keeps_conceptual_top_five_stable(
    integration_app,
    integration_client,
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.READY,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        node_specs = [
            (
                "svc.repository_readiness_guard",
                "repository_readiness_guard",
                "Repository readiness guard for repo-scoped operations.",
                _vector(1.0, 0.0),
            ),
            (
                "svc.ensure_repository_ready_guard",
                "ensure_repository_ready_guard",
                "Ensure the repository readiness guard passes before work begins.",
                _vector(0.98, 0.2),
            ),
            (
                "svc.repository_guard_for_readiness_checks",
                "repository_guard_for_readiness_checks",
                "Run repository readiness guard checks during indexing transitions.",
                _vector(0.93, 0.35),
            ),
            (
                "svc.readiness_guard_repository_banner",
                "readiness_guard_repository_banner",
                "Render the repository readiness guard banner for operators.",
                _vector(0.86, 0.5),
            ),
            (
                "svc.guard_repository_readiness_summary",
                "guard_repository_readiness_summary",
                "Summarise repository readiness guard state for status pages.",
                _vector(0.7, 0.72),
            ),
        ]

        expected_names: list[str] = []
        for index, (qualified_name, name, doc_comment, embedding) in enumerate(
            node_specs, start=1
        ):
            node = CodeNode(
                repository_id=repository.id,
                source_file_id=None,
                file_path="svc.py",
                qualified_name=qualified_name,
                symbol_key=qualified_name,
                node_type=CodeNodeType.FUNCTION,
                name=name,
                language="python",
                start_line=index * 10,
                end_line=index * 10 + 4,
                start_byte=None,
                end_byte=None,
                content=(
                    f"def {name}() -> None:\n"
                    f'    """{doc_comment}"""\n'
                    "    return None"
                ),
                signature=f"def {name}() -> None",
                doc_comment=doc_comment,
                summary=None,
                role=None,
                parent_id=None,
                callers=[],
                callees=[],
                node_metadata={},
                content_hash=f"concept-hash-{index}",
            )
            session.add(node)
            await session.flush()
            session.add(
                CodeEmbedding(
                    code_node_id=node.id,
                    embedding=embedding,
                    model="fake-embed-v1",
                    content_hash=node.content_hash,
                    neighbor_hash=f"concept-neighbor-{index}",
                )
            )
            expected_names.append(qualified_name)

        await session.commit()

    integration_app.dependency_overrides[get_query_embed_provider] = lambda: _StubEmbedProvider()
    try:
        first = await integration_client.post(
            "/api/retrieve",
            json={
                "query": "repository readiness guard",
                "repository_id": str(repository.id),
                "stores": ["code"],
                "top_k": 5,
                "include": {"chunks": False, "graph": False, "scores": True},
            },
        )
        second = await integration_client.post(
            "/api/retrieve",
            json={
                "query": "repository readiness guard",
                "repository_id": str(repository.id),
                "stores": ["code"],
                "top_k": 5,
                "include": {"chunks": False, "graph": False, "scores": True},
            },
        )
    finally:
        integration_app.dependency_overrides.pop(get_query_embed_provider, None)

    assert first.status_code == 200
    assert second.status_code == 200

    def _qualified_names(response) -> list[str]:
        return [
            item["provenance"]["qualified_name"]
            for item in response.json()["results"]
            if item["layer"] == "code"
        ]

    first_order = _qualified_names(first)
    second_order = _qualified_names(second)

    assert first_order[0] == "svc.repository_readiness_guard"
    assert set(first_order) == set(expected_names)
    assert set(second_order) == set(expected_names)
    baseline_positions = {name: index for index, name in enumerate(first_order)}
    followup_positions = {name: index for index, name in enumerate(second_order)}
    for name in expected_names:
        assert abs(baseline_positions[name] - followup_positions[name]) <= 1
    assert set(first.json()["results"][0]["metadata"]["candidate_from"]) >= {"vector", "lexical"}
