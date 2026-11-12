"""The router finds a query name in repository document text on Postgres.

There the lookup matches `content_tsv` in raw SQL, which the SQLite unit
tests never run.
"""
from __future__ import annotations

import pytest

from backend.app.models.enums import RepositoryStatus, RepositoryVisibility
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.rag.source_router import route_sources

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

FLOW = "hand_over:\n  onDone: ShipmentDispatchSucceeded\n"


async def test_route_finds_identifier_in_document_text(pg_session, settings) -> None:
    repos = [
        Repository(
            host="example.test",
            owner="acme",
            name=name,
            git_url=f"https://example.test/acme/{name}.git",
            branch="main",
            status=RepositoryStatus.READY,
            visibility=RepositoryVisibility.PUBLIC,
        )
        for name in ("dispatch", "handbook")
    ]
    pg_session.add_all(repos)
    await pg_session.flush()
    doc = RepoDocument(
        repository_id=repos[0].id,
        file_path="flows/dispatch.yml",
        title="dispatch",
        content=FLOW,
        content_hash="d" * 64,
        bytes=len(FLOW),
        kind="YAML",
    )
    pg_session.add(doc)
    await pg_session.flush()
    pg_session.add(
        RepoDocumentChunk(
            document_id=doc.id, chunk_index=0, heading_path=[], content=FLOW, mentions=[]
        )
    )
    await pg_session.flush()

    hits = await route_sources(
        pg_session,
        query="which flow emits ShipmentDispatchSucceeded",
        current_user=None,
        settings=settings,
        top_k=3,
    )

    [hit] = [h for h in hits if h.kind == "repository"]
    assert hit.label == "example.test/acme/dispatch"
    assert "documents" in hit.why
