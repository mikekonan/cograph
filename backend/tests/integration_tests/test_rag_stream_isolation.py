"""One failing search stream must not take the others down with it.

All streams of a retrieve share one session and one transaction. Without a
savepoint per stream, a SQL error in one aborts the transaction: every later
stream fails too, and the caller gets an empty answer or a dead session.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

import pytest
import sqlalchemy as sa

from backend.app.rag.hybrid import HybridRetriever, VectorRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.rerank import NullReranker
from backend.app.rag.router import RerankRouter

pytestmark = pytest.mark.integration

DIM = 1536
QUERY = "plan_route parcel"


def _unit(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


async def _seed(session) -> uuid.UUID:
    repo_id = (
        await session.execute(
            sa.text(
                "insert into repositories (git_url, name, owner, host, status) "
                "values (:url, 'planner', 'acme', 'example.test', 'ready') returning id"
            ),
            {"url": f"https://example.test/acme/planner-{uuid.uuid4().hex}.git"},
        )
    ).scalar_one()
    for i in range(3):
        body = f"def plan_route_{i}(parcel):\n    # parcel route planner step {i}\n    return parcel"
        node_id = (
            await session.execute(
                sa.text(
                    "insert into code_nodes (repository_id, file_path, qualified_name, node_type,"
                    " name, language, start_line, end_line, content, content_hash)"
                    " values (:r, 'planner/route.py', :q, 'function', :n, 'python', :a, :b, :c, :h)"
                    " returning id"
                ),
                {
                    "r": repo_id,
                    "q": f"planner.route.plan_route_{i}",
                    "n": f"plan_route_{i}",
                    "a": 1 + i * 5,
                    "b": 4 + i * 5,
                    "c": body,
                    "h": hashlib.sha1(body.encode()).hexdigest(),
                },
            )
        ).scalar_one()
        await session.execute(
            sa.text(
                "insert into code_embeddings (id, code_node_id, model, content_hash, embedding)"
                " values (gen_random_uuid(), :n, 'test', :h, CAST(:v AS vector))"
            ),
            {"n": node_id, "h": str(i), "v": "[" + ",".join(map(str, _unit(i))) + "]"},
        )
    return repo_id


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    ("query", "embedding"),
    [
        # pgvector rejects NaN: the first stream (code vector) fails.
        pytest.param(QUERY, [float("nan")] + [0.0] * (DIM - 1), id="first-stream-fails"),
        # Postgres rejects NUL in text: lexical and symbol fail after vector ran.
        pytest.param(QUERY + "\x00", _unit(0), id="middle-stream-fails"),
    ],
)
async def test_one_failing_stream_leaves_the_others_working(
    pg_session, caplog, query: str, embedding: list[float]
) -> None:
    repo_id = await _seed(pg_session)
    retriever = HybridRetriever(
        vector=VectorRetriever(),
        lexical=LexicalRetriever(),
        symbol=SymbolLookup(),
        reranker=NullReranker(),
        router=RerankRouter(enabled=False),
    )

    with caplog.at_level(logging.WARNING, logger="backend.app.rag.hybrid"):
        hits = await retriever.retrieve(
            pg_session,
            query_text=query,
            query_embedding=embedding,
            repository_id=repo_id,
            stores={"code", "repo_docs"},
            top_k=10,
        )

    assert hits, "the healthy streams must still answer"
    assert "failed" in caplog.text, "the broken stream is still logged"
    # The context builder reads on this session next; it must still be usable.
    assert (await pg_session.execute(sa.text("select 1"))).scalar_one() == 1
