"""Unit tests for SummaryGenerator — no DB required; session and importance are mocked."""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.graph.importance import ImportanceResult
from backend.app.llm.completion import CompletionProviderError, FakeCompletionProvider
from backend.app.llm.summary_generator import (
    SummaryGenerator,
    _neighbor_hash,
    _node_content_hash,
    _subgraph_content_hash,
)
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.code_subgraph_summary import CodeSubgraphSummary
from backend.app.models.enums import CodeNodeType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _node(
    uid: uuid.UUID | None = None,
    *,
    qualified_name: str = "module.func",
    content: str = "def func(): pass",
    callers: list | None = None,
    callees: list | None = None,
    signature: str | None = None,
    doc_comment: str | None = None,
) -> MagicMock:
    n = MagicMock()
    n.id = uid or _uid()
    n.qualified_name = qualified_name
    n.content = content
    n.content_hash = hashlib.sha256(content.encode()).hexdigest()
    n.callers = callers or []
    n.callees = callees or []
    n.signature = signature
    n.doc_comment = doc_comment
    n.node_type = CodeNodeType.FUNCTION
    return n


def _summary_mock(
    node_id: uuid.UUID,
    content_hash: str,
    neighbor_hash: str,
    model: str,
    summary_id: uuid.UUID | None = None,
) -> MagicMock:
    s = MagicMock()
    s.id = summary_id or _uid()
    s.code_node_id = node_id
    s.content_hash = content_hash
    s.neighbor_hash = neighbor_hash
    s.model = model
    return s


def _make_session(
    nodes: list,
    node_summaries: list | None = None,
    subgraph_summaries: list | None = None,
    *,
    get_return: MagicMock | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession with predictable scalars() responses.

    scalars() call order inside generate():
      1. select(CodeNode) → nodes
      2. select(CodeNodeSummary) → existing node summaries
      3. select(CodeSubgraphSummary) → existing subgraph summaries
    """
    responses = [nodes, node_summaries or [], subgraph_summaries or []]
    _idx = [0]

    session = AsyncMock()

    async def _scalars(_stmt):
        result = MagicMock()
        r = responses[_idx[0]] if _idx[0] < len(responses) else []
        _idx[0] += 1
        result.all.return_value = r
        return result

    session.scalars = _scalars
    session.get = AsyncMock(return_value=get_return)

    added: list = []
    session.add = MagicMock(side_effect=added.append)
    session._added = added  # expose for assertions

    return session


def _importance(*nodes: MagicMock, start: float = 1.0, step: float = 0.1) -> ImportanceResult:
    """Assign descending importance scores (first node = highest)."""
    scores = {n.id: start - i * step for i, n in enumerate(nodes)}
    return ImportanceResult(scores=scores)


async def _run(
    generator: SummaryGenerator,
    session: AsyncMock,
    importance: ImportanceResult,
    repository_id: uuid.UUID | None = None,
):
    repo_id = repository_id or _uid()
    with patch(
        "backend.app.llm.summary_generator.compute_importance",
        new=AsyncMock(return_value=importance),
    ):
        return await generator.generate(session=session, repository_id=repo_id)


def _compute_hashes(node: MagicMock, all_nodes: list[MagicMock]) -> tuple[str, str]:
    """Return (content_hash, neighbor_hash) using the production hash functions."""
    id_to_qname = {str(n.id): n.qualified_name for n in all_nodes}
    return _node_content_hash(node, id_to_qname), _neighbor_hash(node, id_to_qname)


# ---------------------------------------------------------------------------
# Skip-on-hash tests
# ---------------------------------------------------------------------------


async def test_skip_on_matching_hash():
    """Node with a matching (content_hash, neighbor_hash, model) row is skipped."""
    node = _node(qualified_name="pkg.entry")
    nodes = [node]
    model = FakeCompletionProvider().model

    chash, nhash = _compute_hashes(node, nodes)
    existing = _summary_mock(node.id, chash, nhash, model)
    imp = _importance(node)

    gen = SummaryGenerator(llm=FakeCompletionProvider(), top_node_fraction=1.0)
    session = _make_session(nodes, node_summaries=[existing])
    result = await _run(gen, session, imp)

    assert result.skipped_nodes == 1
    assert result.generated_nodes == 0


async def test_regenerates_on_model_change():
    """Existing summary with a different model triggers regeneration."""
    node = _node(qualified_name="pkg.entry")
    nodes = [node]

    chash, nhash = _compute_hashes(node, nodes)
    existing = _summary_mock(node.id, chash, nhash, model="old-model-v1")
    imp = _importance(node)

    gen = SummaryGenerator(llm=FakeCompletionProvider(), top_node_fraction=1.0)
    session = _make_session(nodes, node_summaries=[existing], get_return=existing)
    result = await _run(gen, session, imp)

    assert result.generated_nodes == 1
    assert result.skipped_nodes == 0


async def test_regenerates_on_content_hash_change():
    """Existing summary with a different content_hash triggers regeneration."""
    node = _node(qualified_name="pkg.entry")
    nodes = [node]

    _, nhash = _compute_hashes(node, nodes)
    stale_chash = "0" * 64
    existing = _summary_mock(node.id, stale_chash, nhash, model=FakeCompletionProvider().model)
    imp = _importance(node)

    gen = SummaryGenerator(llm=FakeCompletionProvider(), top_node_fraction=1.0)
    session = _make_session(nodes, node_summaries=[existing], get_return=existing)
    result = await _run(gen, session, imp)

    assert result.generated_nodes == 1
    assert result.skipped_nodes == 0


async def test_regenerates_on_neighbor_hash_change():
    """Existing summary with a different neighbor_hash triggers regeneration."""
    node = _node(qualified_name="pkg.entry")
    nodes = [node]

    chash, _ = _compute_hashes(node, nodes)
    stale_nhash = "deadbeef00000000"
    existing = _summary_mock(node.id, chash, stale_nhash, model=FakeCompletionProvider().model)
    imp = _importance(node)

    gen = SummaryGenerator(llm=FakeCompletionProvider(), top_node_fraction=1.0)
    session = _make_session(nodes, node_summaries=[existing], get_return=existing)
    result = await _run(gen, session, imp)

    assert result.generated_nodes == 1
    assert result.skipped_nodes == 0


# ---------------------------------------------------------------------------
# Top-node selection tests
# ---------------------------------------------------------------------------


async def test_top_node_selection_floor_with_20_nodes():
    """20 nodes + fraction=0.2 → fraction yields 4, floor bumps to 10 selected."""
    nodes = [_node(qualified_name=f"mod.f{i}") for i in range(20)]
    # Give each node a distinct score; highest first so top-10 are deterministic.
    scores = {n.id: 1.0 - i * 0.01 for i, n in enumerate(nodes)}
    imp = ImportanceResult(scores=scores)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=0.2,
        top_subgraph_count=0,  # skip subgraphs
    )
    session = _make_session(nodes)
    result = await _run(gen, session, imp)

    # floor = 10, fraction = int(20*0.2) = 4, n_target = max(10,4) = 10
    assert result.generated_nodes == 10
    assert result.skipped_nodes == 0


async def test_top_node_selection_fraction_with_100_nodes():
    """100 nodes + fraction=0.2 → 20 selected (fraction > floor)."""
    nodes = [_node(qualified_name=f"mod.f{i}") for i in range(100)]
    scores = {n.id: 1.0 - i * 0.001 for i, n in enumerate(nodes)}
    imp = ImportanceResult(scores=scores)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=0.2,
        top_subgraph_count=0,
    )
    session = _make_session(nodes)
    result = await _run(gen, session, imp)

    # n_target = max(10, int(100*0.2)) = max(10, 20) = 20
    assert result.generated_nodes == 20
    assert result.skipped_nodes == 0


# ---------------------------------------------------------------------------
# Subgraph member cap test
# ---------------------------------------------------------------------------


async def test_subgraph_member_cap_at_20():
    """Root node with 25 callers produces a subgraph with at most 20 members."""
    callers = [_node(qualified_name=f"mod.caller_{i}") for i in range(25)]
    root = _node(
        qualified_name="mod.root",
        callers=[str(c.id) for c in callers],
    )
    all_nodes = [root] + callers

    # Root has highest importance; use top_subgraph_count=1 to test only root's subgraph.
    scores = {n.id: (100.0 if n is root else 1.0) for n in all_nodes}
    imp = ImportanceResult(scores=scores)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=1,
    )
    session = _make_session(all_nodes)
    await _run(gen, session, imp)

    subgraph_adds = [obj for obj in session._added if isinstance(obj, CodeSubgraphSummary)]
    assert len(subgraph_adds) == 1, "exactly one subgraph summary expected"
    root_sg = subgraph_adds[0]
    assert len(root_sg.member_node_ids) <= 20, (
        f"subgraph must have at most 20 members; got {len(root_sg.member_node_ids)}"
    )
    assert root_sg.member_node_ids[0] == root.id, "root must be first member"


# ---------------------------------------------------------------------------
# Concurrency limit test
# ---------------------------------------------------------------------------


class _ConcurrencyTracker:
    """Completion provider that measures peak concurrent calls."""

    def __init__(self, delay: float = 0.01) -> None:
        self._delay = delay
        self._current = 0
        self.max_concurrent = 0

    @property
    def model(self) -> str:
        return "tracker-v1"

    async def complete(self, prompt: str) -> str:
        self._current += 1
        self.max_concurrent = max(self.max_concurrent, self._current)
        await asyncio.sleep(self._delay)
        self._current -= 1
        return "summary"


async def test_concurrency_limit_honoured():
    """Generator must not exceed node_concurrency simultaneous LLM calls."""
    node_concurrency = 3
    n_nodes = 9  # enough to saturate the semaphore

    nodes = [_node(qualified_name=f"mod.f{i}") for i in range(n_nodes)]
    scores = {n.id: float(n_nodes - i) for i, n in enumerate(nodes)}
    imp = ImportanceResult(scores=scores)

    tracker = _ConcurrencyTracker(delay=0.02)
    gen = SummaryGenerator(
        llm=tracker,
        top_node_fraction=1.0,
        top_subgraph_count=0,
        node_concurrency=node_concurrency,
    )
    session = _make_session(nodes)
    await _run(gen, session, imp)

    assert tracker.max_concurrent <= node_concurrency, (
        f"expected max concurrent ≤ {node_concurrency}, got {tracker.max_concurrent}"
    )
    # Must have been saturated (≥ concurrency limit) to prove the semaphore was relevant.
    assert tracker.max_concurrent == node_concurrency, (
        f"expected saturation at {node_concurrency} concurrent calls"
    )


# ---------------------------------------------------------------------------
# Regression helpers
# ---------------------------------------------------------------------------


class _FlakyProvider:
    """Raises RuntimeError on exactly the Nth LLM call; succeeds otherwise."""

    def __init__(self, fail_on: int = 2) -> None:
        self._fail_on = fail_on
        self._count = 0

    @property
    def model(self) -> str:
        return "flaky-v1"

    async def complete(self, prompt: str) -> str:
        self._count += 1
        if self._count == self._fail_on:
            raise RuntimeError("flaky: simulated LLM failure")
        return "summary"


class _FixedModelProvider:
    """Always succeeds; model name is configurable (used to match a prior run's model)."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    @property
    def model(self) -> str:
        return self._model_name

    async def complete(self, prompt: str) -> str:
        return "summary"


class _AlwaysFailProvider:
    """Always raises RuntimeError — tests the all-fail branch."""

    @property
    def model(self) -> str:
        return "always-fail-v1"

    async def complete(self, prompt: str) -> str:
        raise RuntimeError("simulated total provider outage")


# ---------------------------------------------------------------------------
# C1: partial failure — batch gather with return_exceptions
# ---------------------------------------------------------------------------


async def test_partial_failure_persists_others():
    """C1: when call #2 of a 3-item batch fails, items #1 and #3 are persisted;
    a subsequent run with a working provider generates the previously-failed item.
    """
    n1 = _node(qualified_name="pkg.f1")
    n2 = _node(qualified_name="pkg.f2")
    n3 = _node(qualified_name="pkg.f3")
    all_nodes = [n1, n2, n3]
    imp = _importance(n1, n2, n3)

    flaky = _FlakyProvider(fail_on=2)
    gen = SummaryGenerator(
        llm=flaky,
        top_node_fraction=1.0,
        top_subgraph_count=0,
        node_concurrency=1,  # serial execution: call order = n1, n2, n3
    )
    session1 = _make_session(all_nodes)
    result1 = await _run(gen, session1, imp)

    assert result1.generated_nodes == 2
    added = [obj for obj in session1._added if isinstance(obj, CodeNodeSummary)]
    persisted_ids = {s.code_node_id for s in added}
    assert n1.id in persisted_ids, "n1 (call 1, success) must be persisted"
    assert n3.id in persisted_ids, "n3 (call 3, success) must be persisted"
    assert n2.id not in persisted_ids, "n2 (call 2, failure) must NOT be persisted"

    # Second run: n1+n3 have matching summaries → skipped; n2 still missing → generated.
    id_to_qname = {str(n.id): n.qualified_name for n in all_nodes}
    ex_n1 = _summary_mock(
        n1.id, _node_content_hash(n1, id_to_qname), _neighbor_hash(n1, id_to_qname), flaky.model
    )
    ex_n3 = _summary_mock(
        n3.id, _node_content_hash(n3, id_to_qname), _neighbor_hash(n3, id_to_qname), flaky.model
    )
    gen2 = SummaryGenerator(
        llm=_FixedModelProvider(flaky.model),
        top_node_fraction=1.0,
        top_subgraph_count=0,
    )
    session2 = _make_session(all_nodes, node_summaries=[ex_n1, ex_n3])
    result2 = await _run(gen2, session2, imp)

    assert result2.generated_nodes == 1, "only the previously-failed n2 must be regenerated"
    assert result2.skipped_nodes == 2, "n1 and n3 must be skipped (hash match)"


# ---------------------------------------------------------------------------
# P1: all-fail regression + negative case
# ---------------------------------------------------------------------------


async def test_generate_raises_when_all_calls_fail():
    """CRITICAL P1 regression: when every LLM call fails, generate() must raise CompletionProviderError."""
    n1 = _node(qualified_name="pkg.f1")
    n2 = _node(qualified_name="pkg.f2")
    all_nodes = [n1, n2]
    imp = _importance(n1, n2)

    gen = SummaryGenerator(
        llm=_AlwaysFailProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=0,
        node_concurrency=1,
    )
    session = _make_session(all_nodes)

    with pytest.raises(CompletionProviderError):
        await _run(gen, session, imp)

    added_summaries = [obj for obj in session._added if isinstance(obj, CodeNodeSummary)]
    assert len(added_summaries) == 0, "no rows must be persisted when all calls fail"


async def test_generate_does_not_raise_when_some_succeed():
    """P1 negative case: partial failure must not raise; succeeded nodes are persisted."""
    n1 = _node(qualified_name="pkg.f1")
    n2 = _node(qualified_name="pkg.f2")
    n3 = _node(qualified_name="pkg.f3")
    all_nodes = [n1, n2, n3]
    imp = _importance(n1, n2, n3)

    gen = SummaryGenerator(
        llm=_FlakyProvider(fail_on=2),  # n2 fails, n1 and n3 succeed
        top_node_fraction=1.0,
        top_subgraph_count=0,
        node_concurrency=1,
    )
    session = _make_session(all_nodes)
    result = await _run(gen, session, imp)

    assert result.generated_nodes == 2
    added = [obj for obj in session._added if isinstance(obj, CodeNodeSummary)]
    persisted_ids = {s.code_node_id for s in added}
    assert n1.id in persisted_ids, "n1 (call 1, success) must be persisted"
    assert n3.id in persisted_ids, "n3 (call 3, success) must be persisted"
    assert n2.id not in persisted_ids, "n2 (call 2, failure) must NOT be persisted"


# ---------------------------------------------------------------------------
# C2: deterministic subgraph member ordering
# ---------------------------------------------------------------------------


async def test_subgraph_members_deterministic():
    """C2: subgraph member list is identical across two runs with the same data."""
    callers = [_node(qualified_name=f"mod.caller_{i}") for i in range(25)]
    root = _node(
        qualified_name="mod.root",
        callers=[str(c.id) for c in callers],
    )
    all_nodes = [root] + callers

    # All callers share the same importance → tie-breaking falls to str(uid).
    scores = {root.id: 100.0, **{c.id: 1.0 for c in callers}}
    imp = ImportanceResult(scores=scores)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=1,
    )

    session1 = _make_session(all_nodes)
    await _run(gen, session1, imp)
    sg1 = [obj for obj in session1._added if isinstance(obj, CodeSubgraphSummary)]
    assert len(sg1) == 1
    members_run1 = list(sg1[0].member_node_ids)

    session2 = _make_session(all_nodes)
    await _run(gen, session2, imp)
    sg2 = [obj for obj in session2._added if isinstance(obj, CodeSubgraphSummary)]
    assert len(sg2) == 1
    members_run2 = list(sg2[0].member_node_ids)

    assert members_run1 == members_run2, "subgraph member ordering must be identical across runs"
    assert len(members_run1) <= 20


# ---------------------------------------------------------------------------
# H-hash: node content hash covers qualified_name
# ---------------------------------------------------------------------------


async def test_node_hash_covers_qname():
    """H-hash node: changing qualified_name produces a different content_hash → regeneration."""
    node = _node(qualified_name="pkg.original_name", content="def func(): pass")
    nodes = [node]

    id_to_qname_orig = {str(node.id): node.qualified_name}
    chash_orig = _node_content_hash(node, id_to_qname_orig)
    nhash = _neighbor_hash(node, id_to_qname_orig)
    existing = _summary_mock(node.id, chash_orig, nhash, FakeCompletionProvider().model)

    imp = _importance(node)
    gen = SummaryGenerator(llm=FakeCompletionProvider(), top_node_fraction=1.0, top_subgraph_count=0)

    # With original name the summary is skipped (hash matches).
    session1 = _make_session(nodes, node_summaries=[existing])
    result1 = await _run(gen, session1, imp)
    assert result1.skipped_nodes == 1

    # Rename the node — same content, different qualified_name.
    node.qualified_name = "pkg.renamed_name"

    # Hash now differs → regeneration triggered.
    session2 = _make_session(nodes, node_summaries=[existing], get_return=existing)
    result2 = await _run(gen, session2, imp)
    assert result2.generated_nodes == 1, "renaming qualified_name must trigger node regeneration"


# ---------------------------------------------------------------------------
# H-hash: subgraph content hash covers member qualified_names
# ---------------------------------------------------------------------------


async def test_subgraph_hash_covers_member_qnames():
    """H-hash subgraph: renaming a member's qualified_name triggers subgraph regeneration."""
    member = _node(qualified_name="pkg.member_original")
    root = _node(qualified_name="pkg.root", callees=[str(member.id)])
    all_nodes = [root, member]

    id_to_node = {n.id: n for n in all_nodes}
    members_list = [root.id, member.id]
    chash_orig = _subgraph_content_hash(members_list, id_to_node)

    existing_sg = MagicMock()
    existing_sg.id = _uid()
    existing_sg.root_node_id = root.id
    existing_sg.content_hash = chash_orig
    existing_sg.model = FakeCompletionProvider().model

    imp = _importance(root, member)
    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=1,
    )

    # First run: hash matches → subgraph skipped.
    session1 = _make_session(all_nodes, subgraph_summaries=[existing_sg])
    result1 = await _run(gen, session1, imp)
    assert result1.skipped_subgraphs == 1

    # Rename the member — same code body, different qualified_name.
    member.qualified_name = "pkg.member_renamed"

    # Hash now differs → subgraph regenerated.
    session2 = _make_session(all_nodes, subgraph_summaries=[existing_sg], get_return=existing_sg)
    result2 = await _run(gen, session2, imp)
    assert result2.generated_subgraphs == 1, (
        "renaming a member's qualified_name must trigger subgraph regeneration"
    )


# ---------------------------------------------------------------------------
# H-drop: prune stale node summaries
# ---------------------------------------------------------------------------


async def test_prune_drops_fallen_nodes():
    """H-drop: DELETE targets code_node_summaries with NOT IN filter; rowcount is returned."""
    n1 = _node(qualified_name="pkg.f1")
    n2 = _node(qualified_name="pkg.f2")
    n3 = _node(qualified_name="pkg.f3")
    live_nodes = [n1, n2, n3]

    imp = _importance(n1, n2, n3)
    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=0,  # skip subgraphs so only one execute call happens
    )

    session = _make_session(live_nodes)
    execute_stmts: list = []
    delete_result = MagicMock()
    delete_result.rowcount = 2

    async def _capture_execute(stmt):
        execute_stmts.append(stmt)
        return delete_result

    session.execute = _capture_execute
    result = await _run(gen, session, imp)

    assert len(execute_stmts) >= 1, "execute must be called at least once for node prune"
    sql = str(execute_stmts[0]).upper()
    assert "NOT IN" in sql, f"DELETE must use NOT IN filter; got:\n{sql}"
    assert "CODE_NODE_SUMMARIES" in sql, f"DELETE must target code_node_summaries; got:\n{sql}"
    assert result.pruned_nodes == 2


# ---------------------------------------------------------------------------
# H-drop: prune stale subgraph summaries
# ---------------------------------------------------------------------------


async def test_prune_drops_fallen_subgraphs():
    """H-drop: DELETE targets code_subgraph_summaries with NOT IN filter; rowcount returned."""
    n1 = _node(qualified_name="pkg.root1")
    n2 = _node(qualified_name="pkg.root2")
    n3 = _node(qualified_name="pkg.root3")
    live_nodes = [n1, n2, n3]

    scores = {n1.id: 3.0, n2.id: 2.0, n3.id: 1.0}
    imp = ImportanceResult(scores=scores)

    gen = SummaryGenerator(
        llm=FakeCompletionProvider(),
        top_node_fraction=1.0,
        top_subgraph_count=2,  # n3 falls out of top-2
    )

    session = _make_session(live_nodes)
    execute_stmts: list = []
    # First execute = node prune (rowcount=0); second = subgraph prune (rowcount=1).
    _results = [MagicMock(rowcount=0), MagicMock(rowcount=1)]
    _idx = [0]

    async def _capture_execute(stmt):
        execute_stmts.append(stmt)
        res = _results[_idx[0]]
        _idx[0] += 1
        return res

    session.execute = _capture_execute
    result = await _run(gen, session, imp)

    assert len(execute_stmts) >= 2, "execute must be called twice (node prune + subgraph prune)"
    sql = str(execute_stmts[1]).upper()
    assert "NOT IN" in sql, f"subgraph DELETE must use NOT IN filter; got:\n{sql}"
    assert "CODE_SUBGRAPH_SUMMARIES" in sql, (
        f"subgraph DELETE must target code_subgraph_summaries; got:\n{sql}"
    )
    assert result.pruned_subgraphs == 1


# ---------------------------------------------------------------------------
# M-D: node_type participates in node content hash
# ---------------------------------------------------------------------------


def test_node_type_participates_in_hash():
    """M-D: two otherwise-identical nodes with different node_type produce different content hashes."""
    node_fn = _node(qualified_name="pkg.Foo", content="pass")
    node_fn.node_type = CodeNodeType.FUNCTION

    node_cls = _node(qualified_name="pkg.Foo", content="pass")
    node_cls.node_type = CodeNodeType.CLASS

    # No callers/callees, so id_to_qname only needs to cover the two nodes.
    id_to_qname = {str(node_fn.id): "pkg.Foo", str(node_cls.id): "pkg.Foo"}

    hash_fn = _node_content_hash(node_fn, id_to_qname)
    hash_cls = _node_content_hash(node_cls, id_to_qname)

    assert hash_fn != hash_cls, "different node_type must produce a different _node_content_hash"


# ---------------------------------------------------------------------------
# M-B: hash shape — full 64-char SHA256 hex digest
# ---------------------------------------------------------------------------


def test_hash_shape_full_length():
    """M-B regression: _node_content_hash and _subgraph_content_hash return full 64-char digests."""
    node = _node(qualified_name="pkg.fn")
    id_to_qname = {str(node.id): node.qualified_name}

    node_hash = _node_content_hash(node, id_to_qname)
    assert len(node_hash) == 64, (
        f"_node_content_hash must be 64 hex chars (no truncation); got {len(node_hash)}: {node_hash!r}"
    )

    sg_hash = _subgraph_content_hash([node.id], {node.id: node})
    assert len(sg_hash) == 64, (
        f"_subgraph_content_hash must be 64 hex chars (no truncation); got {len(sg_hash)}: {sg_hash!r}"
    )
