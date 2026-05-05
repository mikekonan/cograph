"""End-to-end snapshot test for the LLM-driven wiki pipeline (`wiki-llm-v1`).

Gated behind ``COGRAPH_INTEGRATION_LLM=1`` because it runs the real LLM. The
test takes an already-indexed repository (graph + repo-doc chunks present) and
runs ``run_wiki_generation`` end-to-end against the live LLM provider. It then
asserts the four wiki-llm-v1 acceptance bands documented in
the wiki snapshot benchmark contract (page count, citation coverage,
unresolved-placeholder rate, wall-clock budget) and writes a Markdown + JSON
snapshot pair next to the recorded baselines.

The test is intentionally a thin scaffold: prerequisites (indexed repo,
provider keys) come from the environment so the same harness works against any
canary repo without committing fixtures or secrets to the source tree.

Required environment:

- ``COGRAPH_INTEGRATION_LLM=1`` — opt in to live-LLM tests.
- ``COGRAPH_INTEGRATION_REPOSITORY_ID`` — UUID of an already-ingested repo.
  The default canary is ``samber/mo`` pinned by the benchmark environment.
- ``COGRAPH_INTEGRATION_SOURCE_COMMIT`` — verified commit SHA the LLM should
  cite against (must match the one indexed for the repository).
- Provider settings from ``backend.app.config`` (LLM API key on
  ``completion.api_key``; embedding provider for retrieval).
- ``TEST_DATABASE_URL`` — Postgres URL where the repo is already ingested.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings
from backend.app.models.repository import Repository
from backend.app.wiki.llm_client import OpenAICompatibleStructuredProvider
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    run_wiki_generation,
)
from backend.app.wiki.queries import WikiQueryService
from backend.app.wiki.retrieval import WikiRetrievalService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("COGRAPH_INTEGRATION_LLM") != "1",
        reason="set COGRAPH_INTEGRATION_LLM=1 to run live wiki snapshot",
    ),
]


# Acceptance bands for the wiki-llm-v1 snapshot scorecard.
PAGE_COUNT_MIN = 5
PAGE_COUNT_MAX = 15
UNRESOLVED_PLACEHOLDER_RATE_MAX = 0.02
WALL_CLOCK_MS_MAX = 90_000

SNAPSHOT_DIR = Path(".cograph/benchmark-snapshots")
DEFAULT_SLUG_HINT = "samber-mo"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is required for wiki snapshot integration test")
    return value


@pytest.fixture
async def live_session() -> AsyncSession:
    pg_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/cograph_test",
    )
    engine = create_async_engine(pg_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_wiki_llm_v1_snapshot(live_session: AsyncSession) -> None:
    repository_id = UUID(_required_env("COGRAPH_INTEGRATION_REPOSITORY_ID"))
    source_commit = _required_env("COGRAPH_INTEGRATION_SOURCE_COMMIT")

    repo = await live_session.get(Repository, repository_id)
    assert repo is not None, f"repository {repository_id} not ingested in test DB"

    settings = get_settings()
    api_key = settings.completion.api_key.get_secret_value()
    if not api_key:
        pytest.skip("completion.api_key must be set for live wiki snapshot")

    from backend.app.rag.runtime import (
        build_hybrid_retriever,
        build_query_embed_provider,
    )

    embedder = build_query_embed_provider(settings)
    if embedder is None:
        pytest.skip("embedding provider not configured; live snapshot needs retrieval")

    llm = OpenAICompatibleStructuredProvider(
        api_url=settings.completion.api_url,
        api_key=api_key,
        model=settings.completion.model,
    )
    retriever = WikiRetrievalService(
        hybrid=build_hybrid_retriever(settings),
        embedder=embedder,
    )

    started = time.monotonic()
    result = await run_wiki_generation(
        session=live_session,
        repository_id=repository_id,
        source_commit=source_commit,
        sync_run_id=None,
        llm=llm,
        retriever=retriever,
        config=WikiGenerationConfig(persist=True),
    )
    wall_clock_ms = int((time.monotonic() - started) * 1000)

    queries = WikiQueryService()
    tree = await queries.list_tree(session=live_session, repository_id=repository_id)
    flat_slugs: list[str] = []
    _flatten_slugs(tree, flat_slugs)
    page_records = [
        await queries.get_page_by_slug(
            session=live_session, repository_id=repository_id, slug=slug
        )
        for slug in flat_slugs
    ]
    assert all(record is not None for record in page_records)

    page_count = len(page_records)
    citation_total = sum(
        len(record.citations) for record in page_records if record is not None
    )
    placeholder_total = result.unresolved_placeholders_total
    placeholder_emitted_total = max(citation_total + placeholder_total, 1)
    unresolved_rate = placeholder_total / placeholder_emitted_total

    pages_with_no_citation = [
        record.slug
        for record in page_records
        if record is not None and not record.citations
    ]

    assert PAGE_COUNT_MIN <= page_count <= PAGE_COUNT_MAX, (
        f"page count {page_count} outside [{PAGE_COUNT_MIN}, {PAGE_COUNT_MAX}]"
    )
    assert not pages_with_no_citation, (
        f"pages without resolved citations: {pages_with_no_citation}"
    )
    assert unresolved_rate <= UNRESOLVED_PLACEHOLDER_RATE_MAX, (
        f"unresolved placeholder rate {unresolved_rate:.3f} > "
        f"{UNRESOLVED_PLACEHOLDER_RATE_MAX:.2f}"
    )
    assert wall_clock_ms <= WALL_CLOCK_MS_MAX, (
        f"wall clock {wall_clock_ms}ms > budget {WALL_CLOCK_MS_MAX}ms"
    )

    _write_snapshot_artifacts(
        repo_slug=os.getenv("COGRAPH_INTEGRATION_SLUG_HINT", DEFAULT_SLUG_HINT),
        result=result,
        page_records=page_records,
        wall_clock_ms=wall_clock_ms,
    )


def _flatten_slugs(nodes, out: list[str]) -> None:
    for node in nodes:
        out.append(node.slug)
        if node.children:
            _flatten_slugs(node.children, out)


def _write_snapshot_artifacts(
    *,
    repo_slug: str,
    result,
    page_records,
    wall_clock_ms: int,
) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    stem = f"wiki-llm-v1-{repo_slug}-{today}"

    payload = {
        "run_id": result.run_id,
        "repository_id": str(result.repository_id),
        "source_commit": result.source_commit,
        "model": result.model,
        "metrics": {
            "pages_planned": result.pages_planned,
            "pages_written": result.pages_written,
            "pages_persisted": result.pages_persisted,
            "pages_skipped": result.pages_skipped,
            "pages_orphaned_deleted": result.pages_orphaned_deleted,
            "unresolved_placeholders_total": result.unresolved_placeholders_total,
            "wall_clock_ms": wall_clock_ms,
        },
        # Phase 29.3 T7: pairwise overlap report — empty list on a clean run.
        # Captured here so reviewers can spot planner drift toward redundant
        # pages across snapshots without re-running the pipeline.
        "plan_quality": {
            "suspicious_pairs": [
                pair.model_dump() for pair in result.plan_quality.suspicious_pairs
            ],
        },
        "pages": [
            {
                "slug": record.slug,
                "title": record.title,
                "parent_slug": record.parent_slug,
                "citation_count": len(record.citations),
            }
            for record in page_records
            if record is not None
        ],
    }
    (SNAPSHOT_DIR / f"{stem}.json").write_text(json.dumps(payload, indent=2) + "\n")

    suspicious_pairs = result.plan_quality.suspicious_pairs
    md_lines = [
        f"# wiki-llm-v1 snapshot — {repo_slug} — {today}",
        "",
        f"- **Repository:** `{result.repository_id}`",
        f"- **Source commit:** `{result.source_commit}`",
        f"- **Model:** `{result.model}`",
        f"- **Wall clock:** {wall_clock_ms} ms",
        f"- **Pages planned / written / persisted:** "
        f"{result.pages_planned} / {result.pages_written} / {result.pages_persisted}",
        f"- **Unresolved placeholders:** {result.unresolved_placeholders_total}",
        f"- **Suspicious overlap pairs (T7):** {len(suspicious_pairs)}",
        "",
        "## Pages",
        "",
        "| Slug | Title | Parent | Citations |",
        "|------|-------|--------|-----------|",
    ]
    for record in page_records:
        if record is None:
            continue
        md_lines.append(
            f"| `{record.slug}` | {record.title} | "
            f"`{record.parent_slug or ''}` | {len(record.citations)} |"
        )
    (SNAPSHOT_DIR / f"{stem}.md").write_text("\n".join(md_lines) + "\n")
