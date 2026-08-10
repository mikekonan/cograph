"""Unit tests for PageRank importance scorer (pure-math, no DB required)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.graph.importance import compute_importance


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _row(id_: uuid.UUID, callees: list[str]):
    r = MagicMock()
    r.id = id_
    r.callees = callees
    return r


def _session(rows: list) -> AsyncMock:
    session = AsyncMock()

    async def _execute(_stmt):
        result = MagicMock()
        result.all.return_value = rows
        return result

    session.execute = _execute
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_repo_returns_empty():
    result = await compute_importance(session=_session([]), repository_id=_uid())
    assert result.scores == {}


@pytest.mark.asyncio
async def test_single_node_score_is_one():
    nid = _uid()
    result = await compute_importance(session=_session([_row(nid, [])]), repository_id=_uid())
    assert abs(result.scores[nid] - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_two_nodes_callee_ranks_higher():
    """A→B: B receives A's rank contribution so B > A."""
    a, b = _uid(), _uid()
    rows = [_row(a, [str(b)]), _row(b, [])]
    result = await compute_importance(session=_session(rows), repository_id=_uid())
    assert result.scores[b] > result.scores[a]


@pytest.mark.asyncio
async def test_disconnected_components_sum_to_one():
    ids = [_uid() for _ in range(4)]
    rows = [_row(i, []) for i in ids]
    result = await compute_importance(session=_session(rows), repository_id=_uid())
    assert abs(sum(result.scores.values()) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_dangling_node_no_nan_or_zero():
    """All-dangling graph: rank redistributed uniformly, no NaN/Inf."""
    ids = [_uid(), _uid()]
    rows = [_row(i, []) for i in ids]
    result = await compute_importance(session=_session(rows), repository_id=_uid())
    for v in result.scores.values():
        assert v == v  # NaN check (NaN != NaN)
        assert v > 0


@pytest.mark.asyncio
async def test_self_loop_handled():
    """Self-loop is stripped: recursive node must not dominate a comparable non-recursive peer."""
    a, b = _uid(), _uid()
    # A calls only itself; B has no edges. After stripping both are dangling → equal rank.
    rows = [_row(a, [str(a)]), _row(b, [])]
    result = await compute_importance(session=_session(rows), repository_id=_uid())
    assert abs(sum(result.scores.values()) - 1.0) < 1e-6
    assert abs(result.scores[a] - result.scores[b]) < 1e-6


@pytest.mark.asyncio
async def test_convergence_chain():
    """A→B→C converges within default max_iter, sum ≈ 1."""
    a, b, c = _uid(), _uid(), _uid()
    rows = [_row(a, [str(b)]), _row(b, [str(c)]), _row(c, [])]
    result = await compute_importance(
        session=_session(rows), repository_id=_uid(), max_iter=50
    )
    assert abs(sum(result.scores.values()) - 1.0) < 1e-5
    assert result.scores[c] > result.scores[b] > result.scores[a]


@pytest.mark.asyncio
async def test_deterministic():
    """Identical inputs produce identical score dicts across two calls."""
    ids = [_uid() for _ in range(5)]
    rows = [
        _row(ids[0], [str(ids[1]), str(ids[2])]),
        _row(ids[1], [str(ids[3])]),
        _row(ids[2], [str(ids[3])]),
        _row(ids[3], [str(ids[4])]),
        _row(ids[4], []),
    ]
    repo = _uid()
    r1 = await compute_importance(session=_session(rows), repository_id=repo)
    r2 = await compute_importance(session=_session(rows), repository_id=repo)
    assert r1.scores == r2.scores


@pytest.mark.asyncio
async def test_self_loop_stripped_not_inflated():
    """H7: a self-loop must not inflate a node's rank vs a comparable non-recursive peer.

    Graph: A→A (self-loop) + A→B + C→B.
    After stripping A's self-loop: A→B and C→B — both feed B equally.
    A and C have no incoming edges from others, so their rank should be comparable.
    """
    a, b, c = _uid(), _uid(), _uid()
    rows = [
        _row(a, [str(a), str(b)]),  # A self-loops and calls B
        _row(b, []),
        _row(c, [str(b)]),           # C calls B (mirrors A without the self-loop)
    ]
    result = await compute_importance(session=_session(rows), repository_id=_uid())

    assert abs(sum(result.scores.values()) - 1.0) < 1e-5
    # B accumulates rank from both A and C → highest score.
    assert result.scores[b] > result.scores[a]
    assert result.scores[b] > result.scores[c]
    # Without self-loop inflation, A and C should rank comparably.
    assert abs(result.scores[a] - result.scores[c]) < 0.05, (
        "self-loop must not inflate A's PageRank vs comparable peer C"
    )
