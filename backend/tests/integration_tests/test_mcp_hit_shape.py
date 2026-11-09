"""`cograph_search_code` on Postgres: one record per node, no module rows.

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
    await _node(session, repo_id, "shipping.PrintLabel", "shipping/label.go", body)
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
