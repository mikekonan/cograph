"""The wiki's doc search leaves YAML out; MCP and REST search keep it.

The condition is raw SQL in two places (vector and lexical), so it is
checked on Postgres: SQLite would not catch a bind it cannot type.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from backend.app.rag.hybrid import HybridRetriever, VectorRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.rerank import NullReranker
from backend.app.rag.router import RerankRouter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

DIM = 1536
EMBEDDING = "[" + ",".join(["1"] + ["0"] * (DIM - 1)) + "]"
ALL = {"docs/old.md", "docs/guide.md", "config/stock.yaml"}
PAGES = {"docs/old.md", "docs/guide.md"}


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
    # `old.md` was stored before the kind column: NULL is a page.
    for path, kind in (("docs/old.md", None), ("docs/guide.md", "Repo Doc"), ("config/stock.yaml", "YAML")):
        doc_id = (
            await session.execute(
                sa.text(
                    "insert into repo_documents (id, repository_id, file_path, title, content,"
                    " content_hash, bytes, kind)"
                    " values (gen_random_uuid(), :r, :p, 'Stock', 'shelf parcel', 'h', 12, :k)"
                    " returning id"
                ),
                {"r": repo_id, "p": path, "k": kind},
            )
        ).scalar_one()
        await session.execute(
            sa.text(
                "insert into repo_document_chunks (id, document_id, chunk_index, heading_path,"
                " content, mentions, embedding)"
                " values (gen_random_uuid(), :d, 0, '[]', 'shelf parcel', '[]', CAST(:v AS vector))"
            ),
            {"d": doc_id, "v": EMBEDDING},
        )
    return repo_id


def _paths(hits) -> set[str]:
    return {hit.metadata["file_path"] for hit in hits}


@pytest.mark.parametrize(("page_documents_only", "expected"), [(False, ALL), (True, PAGES)])
async def test_doc_search_filters_yaml_only_when_asked(
    pg_session, page_documents_only: bool, expected: set[str]
) -> None:
    repo_id = await _seed(pg_session)
    common = {
        "store": "repo_docs",
        "repository_id": repo_id,
        "page_documents_only": page_documents_only,
    }

    vector = await VectorRetriever().search(
        pg_session, query_embedding=[1.0] + [0.0] * (DIM - 1), **common
    )
    lexical = await LexicalRetriever().search(pg_session, query_text="parcel", **common)
    hybrid = await HybridRetriever(
        vector=VectorRetriever(),
        lexical=LexicalRetriever(),
        symbol=SymbolLookup(),
        reranker=NullReranker(),
        router=RerankRouter(enabled=False),
    ).retrieve(
        pg_session,
        query_text="parcel",
        query_embedding=[1.0] + [0.0] * (DIM - 1),
        repository_id=repo_id,
        stores={"repo_docs"},
        top_k=10,
        page_documents_only=page_documents_only,
    )

    assert _paths(vector) == expected
    assert _paths(lexical) == expected
    assert _paths(hybrid) == expected
