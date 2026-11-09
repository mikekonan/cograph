"""Lexical code search: any query word matches, rows with every word first.

Postgres only: the query is a tsquery the server parses, so only a real
server proves that no user text breaks its syntax.
"""

from __future__ import annotations

import logging
import uuid

import pytest
import sqlalchemy as sa

from backend.app.rag.hybrid import HybridRetriever, VectorRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.rerank import NullReranker
from backend.app.rag.router import RerankRouter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_EMBEDDING = [1.0] + [0.0] * 1535


async def _node(session, repo_id, qualified_name, content, node_type="function") -> None:
    await session.execute(
        sa.text(
            "insert into code_nodes (repository_id, file_path, qualified_name, node_type,"
            " name, language, start_line, end_line, content, content_hash)"
            " values (:r, 'shipping/label.go', :q, :t, :n, 'go', 1, 3, :c, :h)"
        ),
        {
            "r": repo_id,
            "q": qualified_name,
            "t": node_type,
            "n": qualified_name.rsplit(".", 1)[-1],
            "c": content,
            "h": qualified_name,
        },
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
    await _node(
        session,
        repo_id,
        "shipping.PrintLabel",
        "func PrintLabel(p Box) {\n\t// print the parcel label\n}",
    )
    # One word only, but many times and in the name: the higher raw rank.
    await _node(
        session,
        repo_id,
        "shipping.WeighParcel",
        "func WeighParcel(parcel Parcel) Parcel {\n\t// parcel parcel parcel\n\treturn parcel\n}",
    )
    await _node(
        session,
        repo_id,
        "shipping/label.go#module",
        "package shipping\n// parcel label parcel label parcel label",
        node_type="module",
    )
    await _node(session, repo_id, "shipping.Sort", "func Sort() {\n\t// метка посылки\n}")
    return repo_id


async def _names(session, repo_id, query: str) -> list[str]:
    hits = await LexicalRetriever().search(
        session, store="code", query_text=query, repository_id=repo_id, top_k=10
    )
    return [hit.metadata["qualified_name"] for hit in hits]


async def test_all_words_rank_first_one_word_still_found(pg_session) -> None:
    repo_id = await _seed(pg_session)

    names = await _names(pg_session, repo_id, "where is the parcel label")

    assert names.index("shipping.PrintLabel") < names.index("shipping.WeighParcel")
    assert "shipping/label.go#module" not in names


# (query, expected names, or None when any answer will do)
_HOSTILE = [
    pytest.param("it's \"quoted\" `parcel`", None, id="quotes"),
    pytest.param("parcel & | ! ( ) : * label", None, id="operators"),
    pytest.param("(parcel:* | !label) & <-> (", None, id="operators-glued"),
    pytest.param(r"C:\path\to\parcel \' \\ label\ ", None, id="backslash"),
    pytest.param("'; drop table code_nodes; --", None, id="sql"),
    pytest.param("!!! ??? ((( ))) *:", [], id="no-words"),
    pytest.param("what is the", [], id="stopwords"),
    pytest.param(" ".join(f"word{i}" for i in range(300)), [], id="300-words"),
    pytest.param("метка посылки", ["shipping.Sort"], id="non-latin"),
]


@pytest.mark.parametrize(("query", "expected"), _HOSTILE)
async def test_hostile_query_never_raises(pg_session, caplog, query, expected) -> None:
    repo_id = await _seed(pg_session)

    # The call cograph_search_code makes.
    names = await _names(pg_session, repo_id, query)
    # cograph_retrieve: a failed stream is only logged, so read the log.
    with caplog.at_level(logging.WARNING, logger="backend.app.rag.hybrid"):
        await HybridRetriever(
            vector=VectorRetriever(),
            lexical=LexicalRetriever(),
            symbol=SymbolLookup(),
            reranker=NullReranker(),
            router=RerankRouter(enabled=False),
        ).retrieve(
            pg_session,
            query_text=query,
            query_embedding=_EMBEDDING,
            repository_id=repo_id,
            stores={"code"},
            top_k=10,
        )

    assert "failed" not in caplog.text
    if expected is not None:
        assert names == expected
