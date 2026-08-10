"""Tests for Stage 2.5 — `cluster_nodes` and the planner's `<clusters>` block."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.wiki.clustering import NodeCluster, cluster_nodes
from backend.app.wiki.context import RepoContext
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.manifests import (
    PublicApiEntry,
    RepoManifests,
)
from backend.app.wiki.pipeline import WikiGenerationConfig, plan_pages
from backend.app.wiki.prompts import (
    PAGE_PLANNER_SYSTEM,
    _format_clusters,
    build_page_planner_user,
)
from backend.app.wiki.schemas import PagePlan, RepoOverview


_VECTOR_DIM = 1536


def _public_api(n: int) -> list[PublicApiEntry]:
    return [
        PublicApiEntry(
            kind="function",
            qualified_name=f"pkg.f{i}",
            file_path="pkg/x.go",
            start_line=1,
            end_line=2,
        )
        for i in range(n)
    ]


def _emb_unit(*, x: float, y: float, dim: int = _VECTOR_DIM) -> list[float]:
    """Embedding whose first 2 entries control cluster identity, rest 0."""
    vec = [0.0] * dim
    vec[0] = x
    vec[1] = y
    norm = (x * x + y * y) ** 0.5 or 1.0
    vec[0] /= norm
    vec[1] /= norm
    return vec


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/clustering-fixture",
        name="clustering-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _seed_node_with_embedding(
    session: AsyncSession,
    *,
    repo_id: UUID,
    qn: str,
    file_path: str,
    embedding: list[float],
    importance: float = 0.5,
    summary: str | None = None,
    node_type: CodeNodeType = CodeNodeType.FUNCTION,
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path=file_path,
        qualified_name=qn,
        node_type=node_type,
        name=qn.rsplit(".", 1)[-1],
        language="go",
        start_line=1,
        end_line=10,
        content=f"// {qn}\n",
        content_hash=f"{hash(qn) % (10**63):064d}",
    )
    session.add(node)
    await session.flush()
    session.add(
        CodeEmbedding(
            code_node_id=node.id,
            embedding=embedding,
            model="fake-emb",
            content_hash=f"{hash(qn + 'e') % (10**63):064d}",
        )
    )
    if summary is not None:
        session.add(
            CodeNodeSummary(
                code_node_id=node.id,
                repository_id=repo_id,
                summary=summary,
                importance=importance,
                content_hash=f"{hash(qn + 's') % (10**63):064d}",
                neighbor_hash="z" * 64,
                model="fake-summary-v1",
            )
        )
    return node


# ------------------------------------------------------------------
# `cluster_nodes` against a sqlite session — happy path + degenerate.
# ------------------------------------------------------------------


async def test_cluster_nodes_returns_empty_when_no_embeddings(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    manifests = RepoManifests(public_api=_public_api(20))
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    assert clusters == []


async def test_cluster_nodes_density_gate_skips_tiny_repo(
    db_session: AsyncSession,
) -> None:
    """A library with a tiny public surface and no generated code should
    skip clustering even when embeddings exist."""
    repo = await _make_repo(db_session)
    # Seed a few embeddings — but density gate should still trigger.
    for i in range(3):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"lib.f{i}",
            file_path="lib/main.go",
            embedding=_emb_unit(x=1.0, y=0.0),
        )
    manifests = RepoManifests(public_api=_public_api(5))
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    assert clusters == []


async def test_cluster_nodes_density_gate_skipped_for_generated_code(
    db_session: AsyncSession,
) -> None:
    """Generated code is the carve-out — even small public surfaces should
    cluster when generated artefacts exist."""
    repo = await _make_repo(db_session)
    # Cluster A: 3 nodes in a generated file
    for i in range(3):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"gen.f{i}",
            file_path="api/routes_gen.go",
            embedding=_emb_unit(x=1.0, y=0.0),
        )
    # Cluster B: 3 nodes in another package
    for i in range(3):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"pkg.h{i}",
            file_path="pkg/handler.go",
            embedding=_emb_unit(x=0.0, y=1.0),
        )
    # Cluster C: 3 nodes in a third location
    for i in range(3):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"util.g{i}",
            file_path="util/helpers.go",
            embedding=_emb_unit(x=-1.0, y=0.0),
        )
    # Cluster D: 3 nodes in a fourth location
    for i in range(3):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"core.k{i}",
            file_path="core/state.go",
            embedding=_emb_unit(x=0.0, y=-1.0),
        )
    manifests = RepoManifests(public_api=_public_api(5))  # tiny surface
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    # Density gate shouldn't fire because generated code is present.
    # We expect 4 clusters (or close to it — HDBSCAN may merge near-clusters).
    assert clusters, "expected at least one cluster despite tiny surface"


async def test_cluster_nodes_populates_centroid_and_files(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    # Build 4 distinct topical clusters so HDBSCAN survives the floor of 4.
    layouts = [
        ("alpha", "alpha/file.go", _emb_unit(x=1.0, y=0.0)),
        ("beta", "beta/file.go", _emb_unit(x=-1.0, y=0.0)),
        ("gamma", "gamma/file.go", _emb_unit(x=0.0, y=1.0)),
        ("delta", "delta/file.go", _emb_unit(x=0.0, y=-1.0)),
    ]
    for prefix, path, vec in layouts:
        for i in range(3):
            await _seed_node_with_embedding(
                db_session,
                repo_id=repo.id,
                qn=f"{prefix}.f{i}",
                file_path=path,
                embedding=vec,
                summary=f"{prefix} summary",
            )
    manifests = RepoManifests(public_api=_public_api(20))
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    assert len(clusters) >= 4, f"expected at least 4 clusters, got {len(clusters)}"
    # Centroid must be one of the seeded qualified names.
    seeded_qns = {f"{p}.f{i}" for p, _, _ in layouts for i in range(3)}
    for cluster in clusters:
        assert cluster.centroid_qn in seeded_qns
        assert cluster.size >= 3
        assert cluster.file_paths


async def _seed_edge(
    session: AsyncSession,
    *,
    repo_id: UUID,
    source: CodeNode,
    target: CodeNode,
    edge_type: str = "calls",
) -> None:
    from backend.app.models.code_edge import CodeEdge

    session.add(
        CodeEdge(
            repository_id=repo_id,
            source_node_id=source.id,
            target_node_id=target.id,
            target_qualified_name=target.qualified_name,
            edge_type=edge_type,
        )
    )
    await session.flush()


async def test_cluster_nodes_centrality_promotes_central_module_over_island(
    db_session: AsyncSession,
) -> None:
    """The user's go-oas3 failure mode in miniature: a small "core" cluster
    that everyone calls into, and a big self-contained "vendored
    framework" cluster that nothing outside it references. The central
    cluster MUST sort first despite the island being larger."""
    repo = await _make_repo(db_session)

    # Cluster CORE (3 nodes, will receive many fan-in edges from outside).
    core_nodes = [
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"core.run{i}",
            file_path="core/runner.go",
            embedding=_emb_unit(x=1.0, y=0.0),
        )
        for i in range(3)
    ]
    # Cluster ISLAND (8 nodes, self-contained sub-framework).
    island_nodes = [
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"vendored.f{i}",
            file_path="validation-framework/lib.go",
            embedding=_emb_unit(x=-1.0, y=0.0),
        )
        for i in range(8)
    ]
    # Two more clusters so we clear the floor of 4.
    other_nodes_a = [
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"http.h{i}",
            file_path="http/handler.go",
            embedding=_emb_unit(x=0.0, y=1.0),
        )
        for i in range(3)
    ]
    other_nodes_b = [
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"util.u{i}",
            file_path="util/helpers.go",
            embedding=_emb_unit(x=0.0, y=-1.0),
        )
        for i in range(3)
    ]

    # Many external callers hit CORE → high external_fanin.
    for caller in other_nodes_a + other_nodes_b:
        await _seed_edge(
            db_session, repo_id=repo.id, source=caller, target=core_nodes[0]
        )
    # ISLAND only has internal edges (members call each other) → low fanin,
    # high self_containment.
    for i in range(len(island_nodes) - 1):
        await _seed_edge(
            db_session,
            repo_id=repo.id,
            source=island_nodes[i],
            target=island_nodes[i + 1],
        )

    manifests = RepoManifests(public_api=_public_api(20))
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    assert len(clusters) >= 4

    # The CORE cluster (centroid in core/) MUST sort before the ISLAND
    # cluster (centroid in validation-framework/) even though the island
    # is bigger.
    core_idx = next(
        i for i, c in enumerate(clusters) if c.centroid_qn.startswith("core.")
    )
    island_idx = next(
        i for i, c in enumerate(clusters) if c.centroid_qn.startswith("vendored.")
    )
    assert core_idx < island_idx, (
        f"central cluster (idx={core_idx}) should sort before "
        f"self-contained island (idx={island_idx})"
    )

    # Centrality fields populated correctly.
    core_cluster = clusters[core_idx]
    island_cluster = clusters[island_idx]
    assert core_cluster.external_fanin >= 6  # one edge per external caller
    assert island_cluster.external_fanin == 0
    assert island_cluster.self_containment > 0.9


def test_format_clusters_emits_centrality_signals() -> None:
    clusters = [
        NodeCluster(
            cluster_id=0,
            member_node_ids=[uuid4()],
            member_qualified_names=["pkg.A"],
            centroid_qn="pkg.A",
            file_paths=["pkg/x.go"],
            suggested_parent_topic=None,
            size=4,
            external_fanin=7,
            self_containment=0.42,
        )
    ]
    block = _format_clusters(clusters)
    assert "external_fanin=7" in block
    assert "self_containment=0.42" in block


def test_planner_system_prompt_documents_centrality_and_required_pages() -> None:
    """The new generic rules must be present in the planner prompt —
    centrality signals and manifest-driven required pages."""
    assert "external_fanin" in PAGE_PLANNER_SYSTEM
    assert "self_containment" in PAGE_PLANNER_SYSTEM
    assert "Required pages from manifests" in PAGE_PLANNER_SYSTEM
    assert "<run_commands>" in PAGE_PLANNER_SYSTEM


async def test_cluster_nodes_falls_below_floor_when_homogeneous(
    db_session: AsyncSession,
) -> None:
    """A repo where every node has the same embedding produces ≤1 HDBSCAN
    cluster — below the floor of 4 — so the function must return []."""
    repo = await _make_repo(db_session)
    for i in range(20):
        await _seed_node_with_embedding(
            db_session,
            repo_id=repo.id,
            qn=f"mono.f{i}",
            file_path=f"mono/file{i % 3}.go",
            embedding=_emb_unit(x=1.0, y=0.0),
        )
    manifests = RepoManifests(public_api=_public_api(20))
    clusters = await cluster_nodes(
        session=db_session, repository_id=repo.id, manifests=manifests
    )
    assert clusters == []


# ------------------------------------------------------------------
# Planner formatting / round-trip
# ------------------------------------------------------------------


def test_format_clusters_emits_centroid_and_parent_topic() -> None:
    clusters = [
        NodeCluster(
            cluster_id=0,
            member_node_ids=[uuid4(), uuid4(), uuid4()],
            member_qualified_names=["pkg.A", "pkg.B", "pkg.C"],
            centroid_qn="pkg.A",
            file_paths=["pkg/x.go", "pkg/y.go"],
            suggested_parent_topic="pkg",
            size=3,
            member_summaries=["does X", "does Y"],
        ),
    ]
    block = _format_clusters(clusters)
    assert "cluster_id=0" in block
    assert "centroid: `pkg.A`" in block
    assert "suggested_parent: pkg" in block
    assert "files: pkg/x.go, pkg/y.go" in block
    assert "sample_members: `pkg.A`, `pkg.B`, `pkg.C`" in block
    assert "- does X" in block


def test_format_clusters_empty_returns_fallback_marker() -> None:
    assert "no clusters available" in _format_clusters([])


def test_build_page_planner_user_renders_clusters_block() -> None:
    overview = RepoOverview(one_line="x", long_description="y")
    context = RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        commit_sha="cafef00d",
        file_tree_hash="a" * 64,
        docs_hash="b" * 64,
        summaries_hash="c" * 64,
        identity_hash="d" * 64,
    )
    clusters = [
        NodeCluster(
            cluster_id=0,
            member_node_ids=[uuid4()],
            member_qualified_names=["pkg.A"],
            centroid_qn="pkg.A",
            file_paths=["pkg/x.go"],
            suggested_parent_topic="pkg",
            size=3,
        )
    ]
    body = build_page_planner_user(
        context=context, overview=overview, clusters=clusters
    )
    assert "<clusters>" in body
    assert "centroid: `pkg.A`" in body
    # Reader questions block still rendered.
    assert "<reader_questions_to_cover>" in body


def test_planner_system_prompt_documents_cluster_workflow() -> None:
    """The system prompt MUST teach the planner how to use the clusters —
    otherwise the new `<clusters>` block becomes dead weight."""
    assert "Cluster-driven planning" in PAGE_PLANNER_SYSTEM
    assert "<clusters>" in PAGE_PLANNER_SYSTEM
    assert "Manifest-driven planning" in PAGE_PLANNER_SYSTEM
    assert "FALLBACK" in PAGE_PLANNER_SYSTEM


# ------------------------------------------------------------------
# `plan_pages` accepts a clusters list
# ------------------------------------------------------------------


def _ctx_for_plan() -> RepoContext:
    return RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        commit_sha="cafef00d",
        file_tree_hash="a" * 64,
        docs_hash="b" * 64,
        summaries_hash="c" * 64,
        identity_hash="d" * 64,
    )


async def test_plan_pages_threads_clusters_into_user_block() -> None:
    fake = FakeStructuredProvider()
    fake.queue(
        json.dumps(
            {
                "pages": [
                    {"slug": "index", "title": "Home", "purpose": "Landing"},
                    {"slug": "alpha", "title": "Alpha", "purpose": "alpha cluster"},
                    {"slug": "beta", "title": "Beta", "purpose": "beta cluster"},
                ]
            }
        )
    )
    overview = RepoOverview(one_line="x", long_description="y")
    clusters = [
        NodeCluster(
            cluster_id=0,
            member_node_ids=[uuid4()],
            member_qualified_names=["pkg.alpha"],
            centroid_qn="pkg.alpha",
            file_paths=["alpha/file.go"],
            suggested_parent_topic="alpha",
            size=4,
        ),
    ]
    plan = await plan_pages(
        llm=fake,
        context=_ctx_for_plan(),
        overview=overview,
        config=WikiGenerationConfig(),
        clusters=clusters,
    )
    assert isinstance(plan, PagePlan)
    assert any(p.slug == "alpha" for p in plan.pages)
    # The user block on the only call must carry the cluster.
    user_block = fake.calls[0]["blocks"][1][0]
    assert "centroid: `pkg.alpha`" in user_block


async def test_plan_pages_defaults_to_empty_clusters_block() -> None:
    fake = FakeStructuredProvider()
    fake.queue(
        json.dumps(
            {
                "pages": [
                    {"slug": "index", "title": "Home", "purpose": "."},
                    {"slug": "api", "title": "API", "purpose": "."},
                    {"slug": "config", "title": "Config", "purpose": "."},
                ]
            }
        )
    )
    overview = RepoOverview(one_line="x", long_description="y")
    plan = await plan_pages(
        llm=fake,
        context=_ctx_for_plan(),
        overview=overview,
        config=WikiGenerationConfig(),
    )
    user_block = fake.calls[0]["blocks"][1][0]
    assert "no clusters available" in user_block
    assert any(p.slug == "api" for p in plan.pages)


# ------------------------------------------------------------------
# Regression — orphan re-rooting still works (cf. test_pipeline_stages.py)
# ------------------------------------------------------------------


async def test_plan_pages_normalizes_orphan_parent_with_clusters() -> None:
    fake = FakeStructuredProvider()
    fake.queue(
        json.dumps(
            {
                "pages": [
                    {"slug": "index", "title": "Home", "purpose": "."},
                    {
                        "slug": "child",
                        "title": "Child",
                        "purpose": ".",
                        "parent_slug": "ghost-parent",
                    },
                    {"slug": "alpha", "title": "Alpha", "purpose": "."},
                ]
            }
        )
    )
    overview = RepoOverview(one_line="x", long_description="y")
    clusters = [
        NodeCluster(
            cluster_id=0,
            member_node_ids=[uuid4()],
            member_qualified_names=["pkg.x"],
            centroid_qn="pkg.x",
            file_paths=["pkg/x.go"],
            suggested_parent_topic="pkg",
            size=3,
        ),
    ]
    plan = await plan_pages(
        llm=fake,
        context=_ctx_for_plan(),
        overview=overview,
        config=WikiGenerationConfig(),
        clusters=clusters,
    )
    child = next(p for p in plan.pages if p.slug == "child")
    # ghost-parent doesn't exist → parent is re-rooted to `index` (flat
    # 2-level wiki contract), not dropped to None.
    assert child.parent_slug == "index"
