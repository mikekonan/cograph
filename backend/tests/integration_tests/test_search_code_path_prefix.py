"""`path_prefix` narrows both code search streams inside SQL.

Filtering in the query, not after it, is what lets `top_k` fill up from the
requested folder. Postgres only: both streams use tsvector and pg_trgm.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from backend.app.rag.lexical import LexicalRetriever, SymbolLookup

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


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
    for folder in ("shipping", "storage"):
        body = f"func PrintLabel(parcel Parcel) {{\n\t// {folder} parcel label\n}}"
        await session.execute(
            sa.text(
                "insert into code_nodes (repository_id, file_path, qualified_name, node_type,"
                " name, language, start_line, end_line, content, content_hash)"
                " values (:r, :p, :q, 'function', 'PrintLabel', 'go', 1, 3, :c, :h)"
            ),
            {
                "r": repo_id,
                "p": f"internal/{folder}/label.go",
                "q": f"{folder}.PrintLabel",
                "c": body,
                "h": folder,
            },
        )
    return repo_id


async def _paths(pg_session, repo_id, path_prefix: str) -> dict[str, set[str]]:
    keyword = await LexicalRetriever().search(
        pg_session,
        store="code",
        query_text="parcel",
        repository_id=repo_id,
        path_prefix=path_prefix,
    )
    symbol = await SymbolLookup().search(
        pg_session,
        query_text="PrintLabel",
        repository_id=repo_id,
        path_prefix=path_prefix,
    )
    return {
        "keyword": {hit.metadata["file_path"] for hit in keyword},
        "symbol": {hit.metadata["file_path"] for hit in symbol},
    }


async def test_path_prefix_keeps_only_hits_in_that_folder(pg_session) -> None:
    repo_id = await _seed(pg_session)
    both = {"internal/shipping/label.go", "internal/storage/label.go"}

    assert await _paths(pg_session, repo_id, "") == {"keyword": both, "symbol": both}
    assert await _paths(pg_session, repo_id, "internal/shipping/") == {
        "keyword": {"internal/shipping/label.go"},
        "symbol": {"internal/shipping/label.go"},
    }
    # Not LIKE: `_` and `%` are plain characters, not wildcards.
    for literal in ("internal/s_", "internal/%"):
        assert await _paths(pg_session, repo_id, literal) == {
            "keyword": set(),
            "symbol": set(),
        }
