"""Generated-wiki pages ride along with search hits; every MCP call is logged."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import select

from backend.app.mcp.services import _wiki_excerpt, _wiki_pages
from backend.app.models.document import Document
from backend.app.models.query_log import QueryLog
from backend.app.models.repository import Repository
from backend.tests.test_mcp_argument_names import _SLUG, _seed
from backend.tests.test_query_logs_mcp_pat_attribution import _wire_inline_recorder


def _page(repo_id, slug, title, *, cites=(), questions=()) -> Document:
    """`cites`: (node id, file path) pairs."""
    return Document(
        repository_id=repo_id,
        doc_type="wiki",
        slug=slug,
        title=title,
        content=f"# {title}\n\nHow {title.lower()} works.\n",
        content_hash=slug,
        source_hash=slug,
        model="m",
        citations=[
            {"id": str(node), "kind": "node", "file_path": path}
            for node, path in cites
        ],
        source_node_ids=[str(node) for node, _ in cites],
        source_repo_doc_chunk_ids=[],
        quality={"covers_questions": list(questions)},
    )


async def test_pages_rank_by_cited_files_then_query_words(db_session) -> None:
    await _seed(db_session)
    repo_id = await db_session.scalar(select(Repository.id))
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    db_session.add_all(
        [
            _page(repo_id, "dock", "Dock scheduling", cites=[(a, "carrier/handoff.go")]),
            _page(
                repo_id,
                "carrier",
                "Carrier handoff",
                cites=[(a, "carrier/handoff.go"), (b, "carrier/route.go")],
            ),
            _page(
                repo_id,
                "pallets",
                "Pallet routing",
                questions=["Which lane does a pallet take?"],
            ),
            _page(repo_id, "retry", "Retry policy", cites=[(c, "retry/policy.go")]),
            _page(repo_id, "lanes", "Lane table", cites=[(d, "lanes/table.go")]),
        ]
    )
    await db_session.commit()

    async def pages(hits, query):
        found = await _wiki_pages(
            db_session,
            repository_id=repo_id,
            hits={str(node): path for node, path in hits},
            query=query,
            chars=200,
        )
        return [page["page"] for page in found]

    other = uuid.uuid4
    # Pages citing more of the hits' files first; two pages at most.
    hits = [(a, "carrier/handoff.go"), (b, "carrier/route.go")]
    assert await pages(hits, "pallet lane") == ["carrier", "dock"]
    # A hit in a cited file counts without being the cited node itself.
    assert await pages([(other(), "retry/policy.go")], "x") == ["retry"]
    # No cited file: the page needs two query words in its title or questions.
    assert await pages([], "which lane for a pallet") == ["pallets"]
    assert await pages([], "pallet retry") == []
    # A one-word query needs one.
    assert await pages([], "retry") == ["retry"]
    # Last resort: two hits in a folder the page cites; one is not enough.
    two = [(other(), "lanes/a.go"), (other(), "lanes/b.go")]
    assert await pages(two, "x") == ["lanes"]
    assert await pages(two[:1], "x") == []
    found = await _wiki_pages(
        db_session,
        repository_id=repo_id,
        hits={str(b): "carrier/route.go"},
        query="x",
        chars=200,
    )
    assert found == [
        {
            "page": "carrier",
            "title": "Carrier handoff",
            "excerpt": "How carrier handoff works.",
        }
    ]


def test_excerpt_comes_from_the_section_naming_the_query() -> None:
    page = (
        "# Dock\n\nDocks take trucks.\n\n"
        "## Overview\n\nA dock has doors and a yard.\n\n"
        "## Scheduling\n\nSlots are booked a day ahead.\n\n"
        "### Late trucks\n\nA late truck loses its slot and waits.\n"
    )
    # The subsection names the word, and so does its parent; the shorter wins.
    assert _wiki_excerpt(page, {"late", "truck"}, 600) == {
        "section": "Late trucks",
        "excerpt": "A late truck loses its slot and waits.",
    }
    assert _wiki_excerpt(page, {"slots"}, 600)["section"] == "Scheduling"
    # No section names a query word: the page's opening prose.
    assert _wiki_excerpt(page, {"forklift"}, 30) == {"excerpt": "Docks take trucks. A dock…"}


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
        return {"results": [], "wiki": [{"page": "dock", "title": "Dock"}]}

    async def _broken(**_kwargs):
        raise RuntimeError("index is rebuilding")

    with patch("backend.app.mcp.tools.search_code.search_code_payload", _payload):
        assert not (await call("cograph_repositories", {"search": "warehouse"})).get(
            "isError"
        )
        await call("cograph_search_code", {"repository": _SLUG, "query": "Pallet"})
    with patch("backend.app.mcp.tools.search_code.search_code_payload", _broken):
        assert (await call("cograph_search_code", {"repository": _SLUG, "query": "Crate"}))[
            "isError"
        ]

    rows = sorted(
        (row.tool_name, row.query_text, row.error_msg, row.wiki_pages)
        for row in await db_session.scalars(select(QueryLog))
    )
    assert rows == [
        ("cograph_repositories", "search=warehouse", None, None),
        # The failure's message, so a failed call can be read without logs.
        ("cograph_search_code", "Crate", "index is rebuilding", None),
        # Logs itself; the server hook must not add a second row.
        ("cograph_search_code", "Pallet", None, ["dock"]),
    ]
