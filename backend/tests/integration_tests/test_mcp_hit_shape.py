"""`cograph_search_code` on Postgres: one record per node, no module rows,
and the callers of every hit from one query.

Postgres only: both search streams use tsvector and pg_trgm.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from backend.app.mcp.services import search_code_payload
from backend.app.rag.context_builder import ContextBuilder
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def _node(session, repo_id, qualified_name, path, content, node_type="function"):
    return (
        await session.execute(
            sa.text(
                "insert into code_nodes (repository_id, file_path, qualified_name, node_type,"
                " name, language, start_line, end_line, content, content_hash, signature)"
                " values (:r, :p, :q, :t, :n, 'go', 1, 3, :c, :h, :s) returning id"
            ),
            {
                "r": repo_id,
                "p": path,
                "q": qualified_name,
                "h": qualified_name,
                "t": node_type,
                "n": qualified_name.rsplit(".", 1)[-1],
                "c": content,
                "s": content.splitlines()[0],
            },
        )
    ).scalar_one()


async def _calls(session, repo_id, source, target, target_name="shipping.PrintLabel"):
    await session.execute(
        sa.text(
            "insert into code_edges (repository_id, source_node_id, target_node_id,"
            " target_qualified_name, edge_type) values (:r, :s, :t, :q, 'calls')"
        ),
        {"r": repo_id, "s": source, "t": target, "q": target_name},
    )


async def _seed(session) -> uuid.UUID:
    repo_id = (
        await session.execute(
            sa.text(
                "insert into repositories (git_url, name, owner, host, status) "
                "values (:url, 'warehouse', 'acme', 'example.test', 'ready') returning id"
            ),
            {"url": f"https://example.test/acme/warehouse-{uuid.uuid4().hex}.git"},
        )
    ).scalar_one()
    body = "func PrintLabel(parcel Parcel) {\n\twrite(parcel)\n}"
    label = await _node(session, repo_id, "shipping.PrintLabel", "shipping/label.go", body)
    # Four callers, listed out of path order. None names the callee in its
    # body, so none of them is a search hit for it.
    callers = [
        await _node(session, repo_id, name, path, f"func {name.split('.')[-1]}() {{}}")
        for name, path in (
            ("warehouse.Dispatch", "warehouse/dispatch.go"),
            ("shipping.Reprint", "shipping/reprint.go"),
            ("warehouse.Audit", "warehouse/audit.go"),
            ("shipping.ShipOrder", "shipping/order.go"),
        )
    ]
    for caller in callers:
        await _calls(session, repo_id, caller, label)
    # The same caller through a second spelling, and a recursive call.
    await _calls(session, repo_id, callers[3], label, target_name="PrintLabel")
    await _calls(session, repo_id, label, label)
    await _node(
        session,
        repo_id,
        "shipping/label.go#module",
        "shipping/label.go",
        f"package shipping\n{body}",
        node_type="module",
    )
    return repo_id


def _services(session) -> SimpleNamespace:
    @asynccontextmanager
    async def _same_session():
        yield session

    return SimpleNamespace(
        session_manager=SimpleNamespace(session=_same_session),
        settings=SimpleNamespace(retrieval=SimpleNamespace(rrf_k=60)),
        lexical=LexicalRetriever(),
        symbol=SymbolLookup(),
        context_builder=ContextBuilder(),
    )


async def test_search_code_returns_one_record_per_node_and_no_module(pg_session) -> None:
    repo_id = await _seed(pg_session)

    payload = await search_code_payload(
        services=_services(pg_session),
        repository_id=repo_id,
        query="PrintLabel",
        top_k=10,
    )

    assert [record["name"] for record in payload["results"]] == ["shipping.PrintLabel"]
    assert payload["results"][0]["code"] == "func PrintLabel(parcel Parcel) {"
    assert payload["results"][0]["at"] == "shipping/label.go:1-3"


async def test_callers_of_every_hit_come_from_one_indexed_query(pg_engine, pg_session) -> None:
    repo_id = await _seed(pg_session)
    services = _services(pg_session)
    statements: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "code_edges" in statement:
            statements.append((statement, parameters))

    sa.event.listen(pg_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        payload = await search_code_payload(
            services=services, repository_id=repo_id, query="PrintLabel", top_k=10
        )
    finally:
        sa.event.remove(pg_engine.sync_engine, "before_cursor_execute", _capture)

    (label,) = payload["results"]
    assert [c["name"] for c in label["called_by"]] == [
        "shipping.ShipOrder",
        "shipping.Reprint",
        "warehouse.Audit",
    ]
    assert label["called_by"][0] == {"name": "shipping.ShipOrder", "at": "shipping/order.go:1-3"}
    assert label["called_by_more"] == 1
    assert len(statements) == 1

    # A hit nobody calls says so with an empty list.
    payload = await search_code_payload(
        services=services, repository_id=repo_id, query="ShipOrder", top_k=10
    )
    (ship,) = [r for r in payload["results"] if r["name"] == "shipping.ShipOrder"]
    assert ship["called_by"] == []

    conn = await pg_session.connection()
    await conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
    statement, parameters = statements[0]
    plan = (await conn.exec_driver_sql("EXPLAIN " + statement, parameters)).scalars().all()
    assert "idx_code_edges_target" in "\n".join(plan)
