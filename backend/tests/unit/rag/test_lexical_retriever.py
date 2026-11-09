"""Unit tests for LexicalRetriever (Phase 7d).

LexicalRetriever wraps PostgreSQL ``ts_rank_cd`` over the ``content_tsv``
columns added in migration 0015 plus the ``content_tsv_simple`` column added
in migration 0018.  These tests pin behaviour that doesn't require a live PG:
input sanitisation, config dispatch, empty-query short-circuit.
"""
from __future__ import annotations

from datetime import UTC, datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.rag.lexical import LexicalRetriever  # type: ignore[import-not-found]


def _session_capturing_sql() -> tuple[AsyncMock, list[tuple[str, dict]]]:
    """Return a session whose execute() records (sql_text, params) and returns []."""
    captured: list[tuple[str, dict]] = []
    session = AsyncMock()

    async def _execute(stmt, params=None):
        captured.append((str(stmt), dict(params or {})))
        result = MagicMock()
        mappings = MagicMock()
        mappings.all.return_value = []
        result.mappings.return_value = mappings
        return result

    session.execute = _execute
    return session, captured


@pytest.mark.asyncio
async def test_empty_query_returns_empty_without_sql():
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    result = await retriever.search(
        session,
        store="code",
        query_text="",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    assert result == []
    assert captured == []  # no SQL issued


@pytest.mark.asyncio
async def test_whitespace_only_query_returns_empty_without_sql():
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    result = await retriever.search(
        session,
        store="repo_docs",
        query_text="   \n  \t  ",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    assert result == []
    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["((", ")):", "a | b", "!*", "how does the"])
async def test_code_query_without_words_returns_empty_without_sql(query: str):
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    result = await retriever.search(
        session,
        store="code",
        query_text=query,
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    assert result == []
    assert captured == []


@pytest.mark.asyncio
async def test_code_query_reaches_sql_only_as_quoted_bound_terms():
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    await retriever.search(
        session,
        store="code",
        query_text="(foo & !bar):* | baz",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    [(sql, params)] = captured
    assert "foo" not in sql
    assert params["any_term"] == "'foo' | 'bar' | 'baz'"
    assert params["all_terms"] == "'foo' & 'bar' & 'baz'"


@pytest.mark.asyncio
async def test_code_store_uses_simple_tsvector_config():
    """Code identifiers benefit from 'simple' tokenizer (no English stemming).

    This test pins that the SQL targets ``content_tsv_simple`` and uses the
    ``simple`` regconfig.
    """
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    await retriever.search(
        session,
        store="code",
        query_text="HttpError",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    [(sql, _params)] = captured
    assert "content_tsv_simple" in sql
    assert "'simple'" in sql or "simple," in sql  # config name appears in the call


@pytest.mark.asyncio
async def test_repo_docs_store_uses_english_tsvector_config():
    """Repo-docs chunks remain on the english config (prose, not code)."""
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    await retriever.search(
        session,
        store="repo_docs",
        query_text="how are retries handled",
        repository_id=uuid.uuid4(),
        top_k=10,
    )
    [(sql, _params)] = captured
    assert "content_tsv" in sql and "content_tsv_simple" not in sql
    assert "'english'" in sql


@pytest.mark.asyncio
async def test_md_collections_store_uses_english_tsvector_config():
    """Md-collections prose uses the english regconfig."""
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    await retriever.search(
        session,
        store="md_collections",
        query_text="how are retries handled",
        collection_id=uuid.uuid4(),
        top_k=10,
    )
    [(sql, params)] = captured
    assert "content_tsv" in sql and "content_tsv_simple" not in sql
    assert "'english'" in sql
    assert params["collection_id"] is not None


@pytest.mark.asyncio
async def test_md_collections_store_requires_collection_id():
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    result = await retriever.search(
        session,
        store="md_collections",
        query_text="anything",
        collection_id=None,
        top_k=10,
    )
    assert result == []
    assert captured == []


@pytest.mark.asyncio
async def test_code_store_applies_temporal_params():
    retriever = LexicalRetriever()
    session, captured = _session_capturing_sql()
    since = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    until = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)

    await retriever.search(
        session,
        store="code",
        query_text="HttpError",
        repository_id=uuid.uuid4(),
        top_k=10,
        since=since,
        until=until,
    )

    [(sql, params)] = captured
    assert "last_changed_at" in sql
    assert params["since"] == since
    assert params["until"] == until
