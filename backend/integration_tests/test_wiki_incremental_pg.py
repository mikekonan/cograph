"""Postgres mirror for the incremental wiki path (nightly).

The sqlite unit suite proves the orchestration (equivalence, budgets,
staleness, lifecycle). What only a real Postgres can witness:

- the `wiki_artifacts` JSONB round-trip through asyncpg, including the
  singleton upsert;
- real pgvector ANN ranking: an unchanged repo yields a byte-identical
  retrieval fingerprint across independent `for_page` calls — the
  property that keeps clean pages clean in production;
- the fingerprint actually moving when a cited node's content changes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db.session import SessionManager
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.rag.runtime import build_hybrid_retriever
from backend.app.wiki.incremental import (
    artifact_reusable,
    bundle_fingerprint,
    load_artifact,
    rehydrate_artifact,
    save_artifact,
)
from backend.app.wiki.retrieval import PageBundle, WikiRetrievalService
from backend.app.wiki.schemas import MindMap, PagePlan, RepoOverview
from backend.app.wiki.version import WIKI_SCHEMA_VERSION

CHAT_MODEL = "gpt-4o-mini"
EMBED_MODEL = "fake-embed-v1"


def _overview() -> RepoOverview:
    return RepoOverview(
        one_line="Payment event processor.",
        long_description="Consumes payment events and routes them downstream.",
    )


def _mindmap() -> MindMap:
    return MindMap(root_concept="svc", entry_points=["svc.alpha"])


def _plan() -> PagePlan:
    return PagePlan.model_validate(
        {
            "pages": [
                {
                    "slug": "index",
                    "title": "Overview",
                    "purpose": "Landing page",
                    "sources_hint": ["svc.py"],
                },
                {
                    "slug": "core",
                    "title": "Core flow",
                    "parent_slug": "index",
                    "purpose": "The processing pipeline",
                    "sources_hint": ["svc.alpha"],
                },
            ]
        }
    )


async def _create_repository(session_manager: SessionManager) -> UUID:
    async with session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:acme/wiki-incremental.git",
            host="example.com",
            name="wiki-incremental",
            owner="acme",
            branch="main",
            status=RepositoryStatus.READY,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.commit()
        return repository.id


@pytest.mark.asyncio
async def test_wiki_artifact_jsonb_round_trip_and_singleton_upsert(
    integration_session_manager: SessionManager,
) -> None:
    repository_id = await _create_repository(integration_session_manager)
    overview, mindmap, plan = _overview(), _mindmap(), _plan()

    async with integration_session_manager.session() as session:
        await save_artifact(
            session,
            repository_id=repository_id,
            sync_run_id=None,
            source_commit="c1",
            structural_hash="struct-1",
            plan_hash="plan-1",
            chat_model=CHAT_MODEL,
            embed_model=EMBED_MODEL,
            overview=overview,
            mindmap=mindmap,
            plan=plan,
        )
        await session.commit()

    # Fresh session: everything below reads what asyncpg actually stored.
    async with integration_session_manager.session() as session:
        artifact = await load_artifact(session, repository_id=repository_id)
        assert artifact is not None
        assert artifact.wiki_schema_version == WIKI_SCHEMA_VERSION
        assert artifact_reusable(
            artifact,
            structural_hash="struct-1",
            chat_model=CHAT_MODEL,
            embed_model=EMBED_MODEL,
        )
        rehydrated = rehydrate_artifact(artifact)
        assert rehydrated is not None
        assert rehydrated.overview == overview
        assert rehydrated.mindmap == mindmap
        assert rehydrated.plan == plan

        await save_artifact(
            session,
            repository_id=repository_id,
            sync_run_id=None,
            source_commit="c2",
            structural_hash="struct-2",
            plan_hash="plan-2",
            chat_model=CHAT_MODEL,
            embed_model=EMBED_MODEL,
            overview=overview,
            mindmap=mindmap,
            plan=plan,
        )
        await session.commit()

    async with integration_session_manager.session() as session:
        rows = (
            (
                await session.execute(
                    select(WikiArtifact).where(
                        WikiArtifact.repository_id == repository_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "save_artifact must upsert the singleton row"
        assert rows[0].source_commit == "c2"
        assert not artifact_reusable(
            rows[0],
            structural_hash="struct-1",
            chat_model=CHAT_MODEL,
            embed_model=EMBED_MODEL,
        ), "structural change must invalidate the artifact"


# ---------------------------------------------------------------------------
# pgvector fingerprint stability
# ---------------------------------------------------------------------------

_NODE_SPECS = [
    ("svc.alpha", "def alpha(event):\n    return route(event)"),
    ("svc.beta", "def beta(event):\n    return enrich(event)"),
    ("svc.gamma", "def gamma(event):\n    return audit(event)"),
]


def _vector(seed: int) -> list[float]:
    head = [1.0 / (seed + 1), float(seed) / 10.0]
    return head + [0.0] * (1536 - len(head))


async def _seed_code_nodes(
    session_manager: SessionManager, repository_id: UUID
) -> dict[str, UUID]:
    node_ids: dict[str, UUID] = {}
    async with session_manager.session() as session:
        for index, (qualified_name, content) in enumerate(_NODE_SPECS):
            node = CodeNode(
                id=uuid4(),
                repository_id=repository_id,
                source_file_id=None,
                file_path="svc.py",
                qualified_name=qualified_name,
                symbol_key=qualified_name,
                node_type=CodeNodeType.FUNCTION,
                name=qualified_name.rsplit(".", 1)[-1],
                language="python",
                start_line=index * 10 + 1,
                end_line=index * 10 + 5,
                start_byte=None,
                end_byte=None,
                content=content,
                signature=content.splitlines()[0].rstrip(":"),
                doc_comment=None,
                summary=None,
                role=None,
                parent_id=None,
                callers=[],
                callees=[],
                node_metadata={},
                content_hash=f"hash-{qualified_name}",
            )
            session.add(node)
            await session.flush()
            session.add(
                CodeEmbedding(
                    code_node_id=node.id,
                    embedding=_vector(index),
                    model=EMBED_MODEL,
                    content_hash=node.content_hash,
                    neighbor_hash=f"nbr-{qualified_name}",
                )
            )
            session.add(
                CodeNodeSummary(
                    code_node_id=node.id,
                    repository_id=repository_id,
                    summary=f"Handles the {node.name} step of event processing.",
                    importance=0.5,
                    content_hash=node.content_hash,
                    neighbor_hash=f"nbr-{qualified_name}",
                    model=CHAT_MODEL,
                )
            )
            node_ids[qualified_name] = node.id
        await session.commit()
    return node_ids


async def _retrieve_bundle(
    session_manager: SessionManager,
    settings: Settings,
    repository_id: UUID,
) -> PageBundle:
    """Fresh retriever + fresh session per call — the way two independent
    sync runs would see the repo."""
    retriever = WikiRetrievalService(
        hybrid=build_hybrid_retriever(settings),
        embedder=FakeEmbedProvider(dims=1536),
    )
    async with session_manager.session() as session:
        return await retriever.for_page(
            session=session,
            repository_id=repository_id,
            purpose="How the service processes payment events",
            sources_hint=["svc.alpha", "svc.py"],
        )


@pytest.mark.asyncio
async def test_unchanged_repo_yields_identical_fingerprint_on_pgvector(
    integration_session_manager: SessionManager,
    integration_settings: Settings,
) -> None:
    repository_id = await _create_repository(integration_session_manager)
    await _seed_code_nodes(integration_session_manager, repository_id)

    first = await _retrieve_bundle(
        integration_session_manager, integration_settings, repository_id
    )
    second = await _retrieve_bundle(
        integration_session_manager, integration_settings, repository_id
    )

    assert first.code_chunks, "retrieval must surface the seeded nodes"
    fp_first = bundle_fingerprint(embed_model=EMBED_MODEL, bundle=first)
    fp_second = bundle_fingerprint(embed_model=EMBED_MODEL, bundle=second)
    assert fp_first == fp_second


@pytest.mark.asyncio
async def test_changed_node_content_moves_fingerprint_on_pgvector(
    integration_session_manager: SessionManager,
    integration_settings: Settings,
) -> None:
    repository_id = await _create_repository(integration_session_manager)
    node_ids = await _seed_code_nodes(integration_session_manager, repository_id)

    before = await _retrieve_bundle(
        integration_session_manager, integration_settings, repository_id
    )
    assert any(
        chunk.code_node_id == node_ids["svc.alpha"] for chunk in before.code_chunks
    ), "svc.alpha must be part of the page evidence for this test to bite"

    async with integration_session_manager.session() as session:
        node = await session.get(CodeNode, node_ids["svc.alpha"])
        assert node is not None
        node.content = "def alpha(event):\n    return route_v2(event)"
        node.content_hash = "hash-svc.alpha-v2"
        await session.commit()

    after = await _retrieve_bundle(
        integration_session_manager, integration_settings, repository_id
    )
    fp_before = bundle_fingerprint(embed_model=EMBED_MODEL, bundle=before)
    fp_after = bundle_fingerprint(embed_model=EMBED_MODEL, bundle=after)
    assert fp_before != fp_after
