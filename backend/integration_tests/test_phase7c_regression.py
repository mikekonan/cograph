"""Phase 7c regression tests — locks Phase 7b retrieval shape and migration round-trip.

Guards:
  - Pipeline run through EMBED_REPO_DOCS (no summaries step) still produces retrieval
    results whose provenance ∈ {code, repo_docs, banks} with no ast_summary fragments.
  - Alembic downgrade 0017→0016 drops the two new tables; upgrade restores them cleanly.

Run:
    COGRAPH_RUN_INTEGRATION=1 uv run pytest backend/integration_tests/test_phase7c_regression.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor
from backend.app.rag.retriever import RagRetriever

pytestmark = pytest.mark.integration

_DIMS = 1536


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _checkout_with_code_and_docs(tmp_path: Path) -> Path:
    """Minimal checkout with one source file and one markdown doc."""
    checkout = tmp_path / "checkout7c"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    docs = checkout / "docs"
    docs.mkdir()
    (docs / "overview.md").write_text(
        "# Overview\n\nThis service greets users.\n\n## Usage\n\nCall greet(name).\n",
        encoding="utf-8",
    )
    return checkout


# ---------------------------------------------------------------------------
# Regression: Phase 7b retrieval shape unchanged after Phase 7c
# ---------------------------------------------------------------------------


async def test_retriever_provenance_unchanged_after_phase7c(
    integration_session_manager,
    tmp_path,
):
    """Pipeline through EMBED_REPO_DOCS (no summaries) still returns Phase 7b provenance.

    Invariant: all result.store values must be in {"code", "repo_docs", "banks"}.
    No "ast_summary" provenance must appear — Phase 7c wires summaries into retrieval
    only in Phase 7d.
    """
    provider = FakeEmbedProvider(dims=_DIMS)
    checkout = _checkout_with_code_and_docs(tmp_path)

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:test/phase7c-regression-provenance.git",
            host="example.com",
            name="phase7c-regression-provenance",
            owner="test",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id

        # Run pipeline WITHOUT summary_generator to validate 7b-shape is preserved.
        processor = RepoSyncProcessor(
            code_embedder_service=CodeEmbedderService(provider, batch_size=64),
            repo_document_embedder_service=RepoDocumentEmbedderService(
                provider, batch_size=64
            ),
            summary_generator=None,  # Phase 7c step explicitly disabled
        )
        sync_result = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert sync_result.summary_result is None, (
        "summary_result must be None when summary_generator is not configured"
    )

    # Build a query vector from a known text so at least one code chunk is returned.
    query_text = "greet"
    query_embedding = (await provider.embed(["function greet\ndef greet(name): pass"]))[
        0
    ]

    async with integration_session_manager.session() as session:
        retriever = RagRetriever()
        results = await retriever.retrieve(
            session,
            query_text=query_text,
            query_embedding=query_embedding,
            repository_id=repo_id,
            stores={"code", "repo_docs"},
            top_k=10,
        )

    assert results, "retriever must return at least one result after pipeline"

    allowed_stores = {"code", "repo_docs", "banks"}
    for r in results:
        assert r.store in allowed_stores, (
            f"result store '{r.store}' is not in {allowed_stores!r}; "
            "Phase 7c must not introduce ast_summary provenance before Phase 7d"
        )
        assert r.store != "ast_summary", (
            "ast_summary provenance must not appear until Phase 7d wires it into retrieval"
        )
        assert "ast_summary" not in r.metadata, (
            "metadata must not contain ast_summary fragment"
        )


# ---------------------------------------------------------------------------
# Regression: migration 0017 round-trip is clean
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_migration_0017_downgrade_upgrade_clean(integration_database_url):
    """Alembic 0017↔0016 round-trip leaves the schema in a consistent state.

    Steps:
      1. Downgrade to 0016 — code_node_summaries and code_subgraph_summaries must vanish.
      2. Upgrade back to head (0017) — both tables must reappear.

    This test is synchronous because alembic's env.py calls asyncio.run() internally,
    which cannot be nested inside a running event loop.
    """
    from alembic import command
    from alembic.config import Config

    alembic_ini = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", integration_database_url)

    async def _table_exists(table: str) -> bool:
        import asyncpg  # noqa: PLC0415

        dsn = integration_database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = $1",
                table,
            )
            return row is not None
        finally:
            await conn.close()

    _new_tables = ("code_node_summaries", "code_subgraph_summaries")

    async def _fetch_one(sql: str, *params) -> object:
        import asyncpg  # noqa: PLC0415

        dsn = integration_database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(sql, *params)
        finally:
            await conn.close()

    try:
        command.downgrade(cfg, "0016_add_chunk_content_hash")

        for table in _new_tables:
            exists = asyncio.run(_table_exists(table))
            assert not exists, f"table '{table}' must not exist after downgrade to 0016"

    finally:
        # Always restore to head so other tests in the session are unaffected.
        command.upgrade(cfg, "head")

    for table in _new_tables:
        exists = asyncio.run(_table_exists(table))
        assert exists, f"table '{table}' must exist after upgrade to head (0017)"

    # Schema integrity: the safety claims around migration 0017 are more than
    # just the two table names — UNIQUE constraints, importance DESC indexes,
    # and the uuid[] column must all be in place after upgrade.
    node_uniq = asyncio.run(
        _fetch_one(
            "SELECT conname FROM pg_constraint WHERE conname = $1 AND contype = 'u'",
            "uq_code_node_summaries_code_node_id",
        )
    )
    assert node_uniq is not None, (
        "UNIQUE constraint uq_code_node_summaries_code_node_id must exist"
    )

    subgraph_uniq = asyncio.run(
        _fetch_one(
            "SELECT conname FROM pg_constraint WHERE conname = $1 AND contype = 'u'",
            "uq_code_subgraph_summaries_repo_root",
        )
    )
    assert subgraph_uniq is not None, (
        "UNIQUE constraint uq_code_subgraph_summaries_repo_root must exist"
    )

    node_idx = asyncio.run(
        _fetch_one(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            "idx_code_node_summaries_repo_importance",
        )
    )
    assert node_idx is not None, (
        "importance-DESC index idx_code_node_summaries_repo_importance must exist"
    )
    assert "DESC" in str(node_idx["indexdef"]).upper(), (
        "node importance index must be ordered DESC for top-K queries"
    )

    subgraph_idx = asyncio.run(
        _fetch_one(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            "idx_code_subgraph_summaries_repo_importance",
        )
    )
    assert subgraph_idx is not None, (
        "importance-DESC index idx_code_subgraph_summaries_repo_importance must exist"
    )
    assert "DESC" in str(subgraph_idx["indexdef"]).upper(), (
        "subgraph importance index must be ordered DESC for top-K queries"
    )

    member_col = asyncio.run(
        _fetch_one(
            "SELECT data_type, udt_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
            "code_subgraph_summaries",
            "member_node_ids",
        )
    )
    assert member_col is not None, "member_node_ids column must exist"
    # PostgreSQL reports array columns as data_type='ARRAY' and udt_name='_uuid'.
    assert member_col["data_type"] == "ARRAY", (
        f"member_node_ids must be an ARRAY column; got {member_col['data_type']!r}"
    )
    assert member_col["udt_name"] == "_uuid", (
        f"member_node_ids element type must be uuid (udt_name=_uuid); got {member_col['udt_name']!r}"
    )
