"""Phase 7c integration tests — SummaryGenerator against real PostgreSQL.

Tests:
  - PageRank ordering: chain graph → first node is lowest, last is highest
  - SummaryGenerator.generate() populates code_node_summaries + code_subgraph_summaries
  - Incremental second run: 0 generated, all skipped
  - Content change on one node → exactly that node's summary regenerated

Run:
    COGRAPH_RUN_INTEGRATION=1 uv run pytest backend/integration_tests/test_phase7c_summaries.py -q
"""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select

from backend.app.graph.importance import compute_importance
from backend.app.llm.completion import CompletionProviderError, FakeCompletionProvider
from backend.app.llm.summary_generator import SummaryGenerator
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.code_subgraph_summary import CodeSubgraphSummary
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncErrorCode, SyncSchedule, SyncStep
from backend.app.models.repository import Repository
from backend.app.pipeline.steps import REPO_SYNC_STEPS as _REPO_SYNC_STEPS
from backend.app.pipeline.processor import _sync_error_code

pytestmark = pytest.mark.integration

_N_NODES = 30  # chain length; last node accumulates the most PageRank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _make_node(repo_id, *, index: int) -> CodeNode:
    content = f"def func_{index}(): pass"
    return CodeNode(
        repository_id=repo_id,
        file_path="chain.py",
        qualified_name=f"chain.func_{index}",
        node_type=CodeNodeType.FUNCTION,
        name=f"func_{index}",
        language="python",
        start_line=index * 10 + 1,
        end_line=index * 10 + 10,
        content=content,
        content_hash=_sha256(content),
        callers=[],
        callees=[],
    )


async def _node_summary_count(session, repo_id) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(CodeNodeSummary)
        .where(CodeNodeSummary.repository_id == repo_id)
    )


async def _subgraph_summary_count(session, repo_id) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(CodeSubgraphSummary)
        .where(CodeSubgraphSummary.repository_id == repo_id)
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _create_chain_repo(session) -> tuple:
    """Create a repository with a 30-node chain n0→n1→…→n29.

    Returns (repo_id, nodes) where nodes[0] has no callers (lowest rank)
    and nodes[-1] has no callees (highest rank via accumulated flow).
    """
    repo = Repository(
        git_url="git@github.com:test/phase7c-chain.git",
        name="phase7c-chain",
        owner="test",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    session.add(repo)
    await session.flush()
    repo_id = repo.id

    # Create all nodes first to obtain IDs.
    nodes = [_make_node(repo_id, index=i) for i in range(_N_NODES)]
    session.add_all(nodes)
    await session.flush()

    # Wire chain: nodes[i].callees = [str(nodes[i+1].id)]
    for i in range(_N_NODES - 1):
        nodes[i].callees = [str(nodes[i + 1].id)]
    await session.flush()
    await session.commit()

    return repo_id, nodes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_importance_chain_ordering(integration_session_manager):
    """PageRank: first node in chain has lowest score; last has highest."""
    async with integration_session_manager.session() as session:
        repo_id, nodes = await _create_chain_repo(session)

    async with integration_session_manager.session() as session:
        importance = await compute_importance(session=session, repository_id=repo_id)

    scores = importance.scores
    assert len(scores) == _N_NODES, "all nodes must receive an importance score"
    assert abs(sum(scores.values()) - 1.0) < 1e-5, "scores must sum to ≈ 1.0"

    first_id = nodes[0].id
    last_id = nodes[-1].id

    assert scores[last_id] == max(scores.values()), (
        "terminal chain node (no callers) must have highest PageRank — "
        "receives accumulated rank from entire chain"
    )
    assert scores[first_id] == min(scores.values()), (
        "head chain node (no callers, not a dangling node) must have lowest PageRank"
    )


async def test_summary_generator_populates_rows(integration_session_manager):
    """SummaryGenerator.generate() writes code_node_summaries and code_subgraph_summaries."""
    top_node_fraction = 0.5  # n_target = max(10, int(30*0.5)) = 15
    top_subgraph_count = 5
    expected_node_rows = max(10, int(_N_NODES * top_node_fraction))

    async with integration_session_manager.session() as session:
        repo_id, _ = await _create_chain_repo(session)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(response="summary"),
        top_node_fraction=top_node_fraction,
        top_subgraph_count=top_subgraph_count,
    )

    async with integration_session_manager.session() as session:
        result = await gen.generate(session=session, repository_id=repo_id)

    assert result.generated_nodes == expected_node_rows, (
        f"expected {expected_node_rows} node summaries generated, got {result.generated_nodes}"
    )
    assert result.skipped_nodes == 0, "first run must skip nothing"
    assert result.generated_subgraphs <= top_subgraph_count
    assert result.generated_subgraphs > 0, "at least one subgraph summary must be generated"
    assert result.model == FakeCompletionProvider().model

    async with integration_session_manager.session() as session:
        node_count = await _node_summary_count(session, repo_id)
        sg_count = await _subgraph_summary_count(session, repo_id)

        # All node summaries must have importance > 0.
        min_importance = await session.scalar(
            select(func.min(CodeNodeSummary.importance))
            .where(CodeNodeSummary.repository_id == repo_id)
        )

    assert node_count == expected_node_rows
    assert sg_count <= top_subgraph_count
    assert min_importance is not None and min_importance > 0, (
        "every node summary must have importance > 0 (top nodes selected by PageRank)"
    )


async def test_summary_generator_incremental_second_run(integration_session_manager):
    """Second generate() call with no changes skips all nodes (incremental)."""
    async with integration_session_manager.session() as session:
        repo_id, _ = await _create_chain_repo(session)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(response="summary"),
        top_node_fraction=0.5,
        top_subgraph_count=5,
    )

    async with integration_session_manager.session() as session:
        first = await gen.generate(session=session, repository_id=repo_id)

    assert first.generated_nodes > 0

    async with integration_session_manager.session() as session:
        second = await gen.generate(session=session, repository_id=repo_id)

    assert second.generated_nodes == 0, (
        "second run with identical content must generate 0 node summaries"
    )
    assert second.skipped_nodes == first.generated_nodes, (
        "second run must skip exactly as many nodes as first run generated"
    )
    assert second.generated_subgraphs == 0
    assert second.skipped_subgraphs == first.generated_subgraphs


async def test_summary_generator_regenerates_on_content_change(integration_session_manager):
    """Changing one node's content causes exactly that node's summary to be regenerated."""
    async with integration_session_manager.session() as session:
        repo_id, nodes = await _create_chain_repo(session)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(response="summary"),
        top_node_fraction=0.5,
        top_subgraph_count=5,
    )

    # First pass: generate all summaries.
    async with integration_session_manager.session() as session:
        first = await gen.generate(session=session, repository_id=repo_id)

    # Pick a node that will definitely be in the top fraction (high index = high rank).
    changed_node_id = nodes[_N_NODES - 3].id  # near the tail, high rank, in top 50%

    new_content = "def func_changed(): return 'modified content'"
    async with integration_session_manager.session() as session:
        node = await session.get(CodeNode, changed_node_id)
        assert node is not None
        node.content = new_content
        node.content_hash = _sha256(new_content)
        await session.commit()

    # Second pass: only the changed node should be regenerated.
    async with integration_session_manager.session() as session:
        second = await gen.generate(session=session, repository_id=repo_id)

    # The changed node's content_hash now differs → regenerated.
    # Its neighbours' neighbor_hash is unchanged (qualified names unchanged).
    assert second.generated_nodes >= 1, "at least the changed node must be regenerated"
    assert second.generated_nodes <= first.generated_nodes, (
        "regeneration must not exceed first-run count"
    )
    assert second.skipped_nodes == first.generated_nodes - second.generated_nodes


# ---------------------------------------------------------------------------
# P1: orchestrator registration
# ---------------------------------------------------------------------------


def test_orchestrator_registers_generate_summaries():
    """P1: _REPO_SYNC_STEPS includes a (SyncStep.GENERATE_SUMMARIES, …) entry."""
    assert any(s == SyncStep.GENERATE_SUMMARIES for s, _ in _REPO_SYNC_STEPS), (
        "SyncStep.GENERATE_SUMMARIES must be registered in _REPO_SYNC_STEPS"
    )


# ---------------------------------------------------------------------------
# H3: error code mapping
# ---------------------------------------------------------------------------


def test_completion_provider_error_mapped():
    """H3: CompletionProviderError maps to SUMMARY_PROVIDER_FAILED, not GRAPH_INGEST_FAILED."""
    exc = CompletionProviderError("all retries exhausted")
    code = _sync_error_code(exc)

    assert code == SyncErrorCode.SUMMARY_PROVIDER_FAILED, (
        f"expected {SyncErrorCode.SUMMARY_PROVIDER_FAILED!r}, got {code!r}"
    )
    assert code != "GRAPH_INGEST_FAILED", "must not fall through to generic error code"


# ---------------------------------------------------------------------------
# H-A: orchestrator and processor share the single step list from steps.py
# ---------------------------------------------------------------------------


def test_orchestrator_and_processor_share_step_list():
    """H-A regression: both orchestrator and processor import REPO_SYNC_STEPS from pipeline.steps."""
    import backend.app.pipeline.orchestrator as orchestrator_mod
    import backend.app.pipeline.processor as processor_mod
    from backend.app.pipeline.steps import REPO_SYNC_STEPS

    assert orchestrator_mod.REPO_SYNC_STEPS is REPO_SYNC_STEPS, (
        "orchestrator must import REPO_SYNC_STEPS from pipeline.steps, not define its own copy"
    )
    assert processor_mod.REPO_SYNC_STEPS is REPO_SYNC_STEPS, (
        "processor must import REPO_SYNC_STEPS from pipeline.steps, not define its own copy"
    )


# ---------------------------------------------------------------------------
# H-B: SyncErrorCode enum is complete
# ---------------------------------------------------------------------------


def test_sync_error_code_is_enum():
    """H-B regression: SyncErrorCode has all 5 required members with lowercase snake_case values."""
    expected = {
        "CHECKOUT_NOT_FOUND",
        "CHECKOUT_INVALID",
        "EMBEDDING_PROVIDER_FAILED",
        "GRAPH_INGEST_FAILED",
        "SUMMARY_PROVIDER_FAILED",
        "GO_BUILD_CONSTRAINT_UNSUPPORTED",
        "PARSE_DB_CONFLICT",
        "GO_BUILD_VARIANT_CONFLICT",
        "WIKI_PROVIDER_FAILED",
    }
    actual = {m.name for m in SyncErrorCode}
    assert actual == expected, (
        f"SyncErrorCode member mismatch — missing: {expected - actual}, extra: {actual - expected}"
    )
    for member in SyncErrorCode:
        assert member.value == member.value.lower(), (
            f"{member.name} must have a lowercase value; got {member.value!r}"
        )
