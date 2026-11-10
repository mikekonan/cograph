"""Generated-wiki pages ride along with search hits; every MCP call is logged."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import select

from backend.app.mcp.services import _wiki_pages
from backend.app.models.document import Document
from backend.app.models.query_log import QueryLog
from backend.app.models.repository import Repository
from backend.tests.test_mcp_argument_names import _SLUG, _seed
from backend.tests.test_query_logs_mcp_pat_attribution import _wire_inline_recorder


def _page(repo_id, slug, title, *, cites=(), questions=()) -> Document:
    return Document(
        repository_id=repo_id,
        doc_type="wiki",
        slug=slug,
        title=title,
        content=f"# {title}\n\nHow {title.lower()} works.\n",
        content_hash=slug,
        source_hash=slug,
        model="m",
        citations=[],
        source_node_ids=[str(node) for node in cites],
        source_repo_doc_chunk_ids=[],
        quality={"covers_questions": list(questions)},
    )


async def test_pages_rank_by_cited_hits_then_query_words(db_session) -> None:
    await _seed(db_session)
    repo_id = await db_session.scalar(select(Repository.id))
    a, b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            _page(repo_id, "dock", "Dock scheduling", cites=[a]),
            _page(repo_id, "carrier", "Carrier handoff", cites=[a, b]),
            _page(
                repo_id,
                "pallets",
                "Pallet routing",
                questions=["Which lane does a pallet take?"],
            ),
            _page(repo_id, "retry", "Retry policy"),
        ]
    )
    await db_session.commit()

    async def pages(node_ids, query):
        found = await _wiki_pages(
            db_session, repository_id=repo_id, node_ids=node_ids, query=query
        )
        return [page["page"] for page in found]

    # Most cited hits first; two pages at most.
    assert await pages([a, b], "pallet lane") == ["carrier", "dock"]
    # No cited hit: the page needs two query words in its title or questions.
    assert await pages([], "which lane for a pallet") == ["pallets"]
    assert await pages([], "pallet retry") == []
    # A one-word query needs one.
    assert await pages([], "retry") == ["retry"]
    found = await _wiki_pages(
        db_session, repository_id=repo_id, node_ids=[b], query="x"
    )
    assert found == [
        {"page": "carrier", "title": "Carrier handoff", "lead": "How carrier handoff works."}
    ]


async def test_every_tool_call_writes_one_query_log_row(
    app, client, db_session, monkeypatch
) -> None:
    _wire_inline_recorder(monkeypatch, app)
    plaintext = await _seed(db_session)

    async def call(name: str, arguments: dict) -> dict:
        response = await client.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {plaintext}",
                "Accept": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert response.status_code == 200
        return response.json()["result"]

    async def _payload(**_kwargs):
        return {"results": []}

    with patch("backend.app.mcp.tools.search_code.search_code_payload", _payload):
        assert not (await call("cograph_repositories", {"search": "warehouse"})).get(
            "isError"
        )
        await call("cograph_search_code", {"repository": _SLUG, "query": "Pallet"})

    rows = sorted(
        (row.tool_name, row.query_text)
        for row in await db_session.scalars(select(QueryLog))
    )
    assert rows == [
        ("cograph_repositories", "search=warehouse"),
        # Logs itself; the server hook must not add a second row.
        ("cograph_search_code", "Pallet"),
    ]
