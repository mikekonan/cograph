"""Unit tests for SymbolLookup (Phase 7d).

SymbolLookup uses pg_trgm similarity over ``code_nodes.qualified_name`` to
catch fuzzy symbol matches that BM25 (token-based) misses — e.g.,
``foo_bar_baz`` should match a query for ``foobarbaz``.  These tests pin the
contract; PG-specific behaviour is verified in the integration regression.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.rag.lexical import SymbolLookup  # type: ignore[import-not-found]


def _session_with_rows(rows: list[dict]) -> tuple[AsyncMock, list[tuple[str, dict]]]:
    captured: list[tuple[str, dict]] = []
    session = AsyncMock()

    async def _execute(stmt, params=None):
        captured.append((str(stmt), dict(params or {})))
        result = MagicMock()
        mappings = MagicMock()
        mappings.all.return_value = rows
        result.mappings.return_value = mappings
        return result

    session.execute = _execute
    return session, captured


@pytest.mark.asyncio
async def test_empty_query_short_circuits_without_sql():
    lookup = SymbolLookup()
    session, captured = _session_with_rows([])
    result = await lookup.search(
        session,
        query_text="",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    assert result == []
    assert captured == []


@pytest.mark.asyncio
async def test_passes_similarity_threshold_to_sql():
    """Default threshold must be ≥ 0.3 (pg_trgm default) and bound as a parameter."""
    lookup = SymbolLookup(similarity_threshold=0.3)
    session, captured = _session_with_rows([])
    await lookup.search(
        session,
        query_text="foobarbaz",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    [(sql, params)] = captured
    assert "similarity" in sql.lower()
    # Threshold should be bound (not interpolated).
    assert 0.3 in params.values()


@pytest.mark.asyncio
async def test_orders_by_similarity_desc_in_sql():
    lookup = SymbolLookup()
    session, captured = _session_with_rows([])
    await lookup.search(
        session,
        query_text="foobar",
        repository_id=uuid.uuid4(),
        top_k=5,
    )
    [(sql, _params)] = captured
    assert "ORDER BY" in sql.upper()
    assert "DESC" in sql.upper()
    assert "similarity" in sql.lower()


@pytest.mark.asyncio
async def test_filters_by_repository_id():
    lookup = SymbolLookup()
    session, captured = _session_with_rows([])
    repo_id = uuid.uuid4()
    await lookup.search(session, query_text="x", repository_id=repo_id, top_k=10)
    [(_sql, params)] = captured
    assert repo_id in params.values()


@pytest.mark.asyncio
async def test_returns_retrieved_chunks_with_symbol_metadata():
    """Hit rows should map to RetrievedChunk(store='code') with qualified_name in metadata."""
    chunk_id = uuid.uuid4()
    rows = [
        {
            "chunk_id": chunk_id,
            "content": "def foo_bar_baz(): ...",
            "qualified_name": "pkg.foo_bar_baz",
            "file_path": "pkg/a.py",
            "language": "python",
            "start_line": 1,
            "end_line": 3,
            "score": 0.85,
        }
    ]
    lookup = SymbolLookup()
    session, _ = _session_with_rows(rows)
    [hit] = await lookup.search(
        session,
        query_text="foobarbaz",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    assert hit.store == "code"
    assert hit.chunk_id == chunk_id
    assert hit.score == 0.85
    assert hit.metadata["qualified_name"] == "pkg.foo_bar_baz"
