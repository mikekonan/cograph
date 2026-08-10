"""Regression tests pinning the asyncpg parameter-type fix on temporal filters.

Without explicit `bindparam(..., type_=DateTime(timezone=True))`, asyncpg fails
with `AmbiguousParameterError` whenever a caller passes
``as_of=since=until=None``: ``:foo IS NULL OR col <op> :foo`` doesn't give
Postgres enough context to infer the type when the value is NULL.

Lives in `integration_tests/` because the unit suites use SQLite, which
silently tolerates the pattern — the asyncpg-specific quirk only surfaces
against a real Postgres. An empty repo is fine; we only need to prove the
prepared statement compiles and executes without the parameter-type error.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.rag.hybrid import VectorRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


_QVEC = [0.1] * 1536
_QUERY = "anything"


@pytest.mark.parametrize("store", ["code", "repo_docs"])
async def test_vector_search_with_none_temporal_filters_does_not_crash(
    pg_session: AsyncSession, store: str
) -> None:
    repo_id = uuid.uuid4()
    result = await VectorRetriever().search(
        pg_session,
        store=store,  # type: ignore[arg-type]
        query_embedding=_QVEC,
        repository_id=repo_id,
        top_k=5,
        as_of=None,
        since=None,
        until=None,
    )
    assert result == []


@pytest.mark.parametrize("store", ["code", "repo_docs"])
async def test_lexical_search_with_none_temporal_filters_does_not_crash(
    pg_session: AsyncSession, store: str
) -> None:
    repo_id = uuid.uuid4()
    result = await LexicalRetriever().search(
        pg_session,
        store=store,  # type: ignore[arg-type]
        query_text=_QUERY,
        repository_id=repo_id,
        top_k=5,
        as_of=None,
        since=None,
        until=None,
    )
    assert result == []


async def test_symbol_lookup_with_none_temporal_filters_does_not_crash(
    pg_session: AsyncSession,
) -> None:
    repo_id = uuid.uuid4()
    result = await SymbolLookup().search(
        pg_session,
        query_text=_QUERY,
        repository_id=repo_id,
        top_k=5,
        as_of=None,
        since=None,
        until=None,
    )
    assert result == []
