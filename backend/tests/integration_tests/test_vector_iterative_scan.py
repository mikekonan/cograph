"""A repository must get its own vector hits even when another one fills the index.

All repositories share one HNSW index, and the repository filter applies after
the index walk. Without `hnsw.iterative_scan` the walk stops at `hnsw.ef_search`
(40) rows: when all of them belong to another repository, the filter leaves
nothing. `relaxed_order` keeps walking until the filter has enough rows.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa

from backend.app.config import DatabaseSettings, RetrievalSettings, Settings
from backend.app.db.session import SessionManager
from backend.app.rag.hybrid import VectorRetriever
from backend.app.rag.retriever import EMBEDDING_DIM

pytestmark = pytest.mark.integration

SMALL, BIG = 20, 2000


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def small_repo_id(pg_engine):
    """Seed both repositories, committed so a fresh pool can see them."""
    async with pg_engine.begin() as conn:
        ids = {}
        # The big repository points along axis 1, the small one along axis 2.
        for name, count, axis in (("big", BIG, 1), ("small", SMALL, 2)):
            ids[name] = (
                await conn.execute(
                    sa.text(
                        "insert into repositories (git_url, name, owner, host, status)"
                        " values (:url, :name, 'acme', 'example.test', 'ready') returning id"
                    ),
                    {"url": f"https://example.test/acme/{name}.git", "name": name},
                )
            ).scalar_one()
            await conn.execute(
                sa.text(
                    "insert into code_nodes (repository_id, file_path, qualified_name,"
                    " node_type, name, language, start_line, end_line, content, content_hash)"
                    " select :repo, :name || '/mod.py', :name || '.f' || g, 'function', 'f' || g,"
                    " 'python', g, g, 'def f' || g || '(): pass', md5(:name || g)"
                    " from generate_series(1, :count) g"
                ),
                {"repo": ids[name], "name": name, "count": count},
            )
            # A small tilt of its own keeps every vector distinct.
            await conn.execute(
                sa.text(
                    "insert into code_embeddings (id, code_node_id, model, content_hash, embedding)"
                    " select gen_random_uuid(), cn.id, 'test', cn.content_hash,"
                    "  (select array_agg(case when i = :axis then 1.0::real"
                    "                         when i = 10 + cn.start_line % 1500 then 0.1::real"
                    "                         else 0::real end order by i)"
                    "   from generate_series(1, :dim) i)::vector"
                    " from code_nodes cn where cn.repository_id = :repo"
                ),
                {"repo": ids[name], "axis": axis, "dim": EMBEDDING_DIM},
            )
        await conn.execute(sa.text("analyze code_nodes, code_embeddings"))
    return ids["small"]


class _Spy:
    """Pass-through session that keeps the last statement, to EXPLAIN it."""

    def __init__(self, session) -> None:
        self.session = session
        self.last = None

    async def execute(self, stmt, params=None):
        self.last = (stmt, params)
        return await self.session.execute(stmt, params)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(("scan", "finds_all"), [("relaxed_order", True), ("off", False)])
async def test_small_repo_finds_all_its_rows(pg_engine, small_repo_id, scan, finds_all) -> None:
    url = pg_engine.url.render_as_string(hide_password=False)
    manager = SessionManager(
        Settings(
            database=DatabaseSettings(url=url),
            retrieval=RetrievalSettings(hnsw_iterative_scan=scan),
        )
    )
    query = [0.0] * EMBEDDING_DIM
    query[0] = 1.0  # towards the big repository
    try:
        # Every MCP read ends in a rollback; the setting must outlive it.
        async with manager.session() as session:
            await session.execute(sa.text("select 1"))
            await session.rollback()

        async with manager.session() as session:
            # Without a sort node the planner can only order through HNSW.
            await session.execute(sa.text("set local enable_sort = off"))
            spy = _Spy(session)
            hits = await VectorRetriever().search(
                spy, store="code", query_embedding=query, repository_id=small_repo_id, top_k=100
            )
            stmt, params = spy.last
            plan = "\n".join(
                row[0] for row in await session.execute(sa.text("explain " + stmt.text), params)
            )
    finally:
        await manager.dispose()

    assert "idx_code_embeddings_hnsw" in plan, plan
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    if finds_all:
        assert len(hits) == SMALL
    else:
        # The rollback switch: the old walk stops among the big repository's rows.
        assert len(hits) < SMALL
