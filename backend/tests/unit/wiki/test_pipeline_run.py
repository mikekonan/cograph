"""End-to-end tests for `run_wiki_generation` (Stages 1-6 with FakeStructuredProvider)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.document import Document
from backend.app.models.repository import Repository
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.pipeline import (
    _CITATION_GATE_MAX_REPAIRS,
    WikiGenerationConfig,
    run_wiki_generation,
)
from backend.app.wiki.retrieval import WikiRetrievalService
from backend.app.wiki.schemas import MindMap, PagePlan, RepoOverview

pytestmark = pytest.mark.asyncio


class _NoopHybrid:
    async def retrieve(
        self,
        session: AsyncSession,
        **_kwargs: Any,
    ) -> list:
        return []


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-pipeline-run",
        name="wiki-llm-pipeline-run",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


def _queue_fake_pipeline(provider: FakeStructuredProvider, *, slugs: list[str]) -> None:
    provider.queue(
        RepoOverview(
            one_line="Pipeline test repo",
            long_description="An end-to-end fake repo for run_wiki_generation tests.",
        ).model_dump_json()
    )
    # Stage 1.5 — mindmap. An empty one is fine; downstream stages tolerate it.
    provider.queue(MindMap().model_dump_json())
    plan = PagePlan.model_validate(
        {
            "pages": [
                {"slug": slug, "title": slug.title(), "purpose": f"about {slug}"}
                for slug in slugs
            ]
        }
    )
    provider.queue(plan.model_dump_json())
    # Stage 4 is the agent loop — one tool turn that calls write_page,
    # followed by an end_turn turn. The dispatcher consumes the markdown
    # and the loop exits.
    for slug in slugs:
        provider.queue_tool_turn(
            tool_uses=[
                (
                    "write_page",
                    {"markdown": f"# {slug.title()}\n\nBody for `{slug}`."},
                )
            ]
        )
        provider.queue_tool_turn(text="")


def _retriever() -> WikiRetrievalService:
    return WikiRetrievalService(
        hybrid=_NoopHybrid(),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
    )


async def test_run_wiki_generation_persists_pages(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    fake = FakeStructuredProvider()
    _queue_fake_pipeline(fake, slugs=["index", "architecture", "getting-started"])

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="cafef00d",
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        config=WikiGenerationConfig(write_concurrency=2),
    )
    assert result.pages_planned == 3
    assert result.pages_written == 3
    assert result.pages_persisted == 3
    assert result.pages_skipped == 0
    assert result.pages_orphaned_deleted == 0
    assert result.errors == []
    # T7: plan-quality report propagates from stages_1_4 to the final result.
    assert result.plan_quality is not None
    assert isinstance(result.plan_quality.suspicious_pairs, list)

    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in rows} == {"index", "architecture", "getting-started"}
    assert all(r.doc_type == "wiki" for r in rows)
    assert all(r.source_commit == "cafef00d" for r in rows)


async def test_run_wiki_generation_skip_persist(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    fake = FakeStructuredProvider()
    _queue_fake_pipeline(fake, slugs=["index", "architecture", "getting-started"])

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        config=WikiGenerationConfig(persist=False),
    )
    assert result.pages_persisted == 0
    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_run_wiki_generation_deletes_orphans(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)

    # First run plants three slugs.
    fake = FakeStructuredProvider()
    _queue_fake_pipeline(fake, slugs=["index", "architecture", "old-page"])
    await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        config=WikiGenerationConfig(),
    )

    # Second run drops `old-page` from the plan — it should be deleted.
    # incremental=False: this test exercises LLM-driven re-planning, which
    # the incremental path deliberately suppresses while the structural
    # hash is unchanged (the artifact plan would be reused instead).
    fake2 = FakeStructuredProvider()
    _queue_fake_pipeline(fake2, slugs=["index", "architecture", "getting-started"])
    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc2",
        sync_run_id=None,
        llm=fake2,
        retriever=_retriever(),
        config=WikiGenerationConfig(incremental=False),
    )
    assert result.pages_orphaned_deleted == 1

    remaining_slugs = {
        row.slug
        for row in (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    }
    assert remaining_slugs == {"index", "architecture", "getting-started"}


async def test_run_wiki_generation_skips_unchanged_content(
    db_session: AsyncSession,
) -> None:
    """Full-rebuild path (incremental=False): rewriting identical bodies
    hits the content-hash skip in the store — decision 4."""
    repo = await _make_repo(db_session)
    fake1 = FakeStructuredProvider()
    _queue_fake_pipeline(fake1, slugs=["index", "architecture", "getting-started"])
    await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake1,
        retriever=_retriever(),
        config=WikiGenerationConfig(incremental=False),
    )

    # Re-running with identical bodies → all three slugs should be skipped.
    fake2 = FakeStructuredProvider()
    _queue_fake_pipeline(fake2, slugs=["index", "architecture", "getting-started"])
    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake2,
        retriever=_retriever(),
        config=WikiGenerationConfig(incremental=False),
    )
    assert result.pages_skipped == 3
    assert result.pages_persisted == 3


async def test_rerun_unchanged_repo_is_incremental_noop(
    db_session: AsyncSession,
) -> None:
    """Incremental path (default): an unchanged repo reuses the artifact,
    clears every page as clean, and makes zero LLM calls on the rerun."""
    repo = await _make_repo(db_session)
    fake1 = FakeStructuredProvider()
    _queue_fake_pipeline(fake1, slugs=["index", "architecture", "getting-started"])
    await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake1,
        retriever=_retriever(),
        config=WikiGenerationConfig(),
    )

    # Nothing queued: any LLM call on the rerun would raise / exhaust.
    fake2 = FakeStructuredProvider()
    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc2",
        sync_run_id=None,
        llm=fake2,
        retriever=_retriever(),
        config=WikiGenerationConfig(),
    )
    assert result.mode == "incremental"
    assert result.pages_clean_skipped == 3
    assert result.pages_written == 0
    assert result.pages_persisted == 0
    assert result.dirty_reasons == {}
    assert fake2.calls == []
    assert fake2.tool_calls == []

    # Clean rows got the audit bump but kept their content.
    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in rows} == {"index", "architecture", "getting-started"}
    assert all(r.source_commit == "abc2" for r in rows)


async def test_run_wiki_generation_records_page_failures(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)

    class _PartialFailure:
        model = "fail-v1"
        _idx = 0

        async def complete_text(self, *, system, blocks, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, *, system, blocks, schema, **_kwargs):
            # Stage 2 + Stage 1.5 + Stage 3 — return overview / mindmap / plan in order.
            if not hasattr(self, "_json_idx"):
                self._json_idx = 0
            self._json_idx += 1
            if self._json_idx == 1:
                return RepoOverview(one_line="x", long_description="y")
            if self._json_idx == 2:
                return MindMap()
            return PagePlan.model_validate(
                {
                    "pages": [
                        {"slug": "index", "title": "I", "purpose": "1"},
                        {"slug": "architecture", "title": "A", "purpose": "2"},
                        {"slug": "getting-started", "title": "G", "purpose": "3"},
                    ]
                }
            )

        async def complete_with_tools(self, **_kwargs):
            from backend.app.wiki.llm_client import (
                StructuredCompletionError,
                ToolUseResult,
            )

            self._idx += 1
            # 1st = index body, 2nd = architecture (raise), 3rd = getting-started.
            if self._idx == 2:
                raise StructuredCompletionError("forced failure")
            await _kwargs["tool_dispatch"](
                "write_page", {"markdown": f"# page-{self._idx}\n\nbody"}
            )
            return ToolUseResult(stop_reason="end_turn", turns_used=1)

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=_PartialFailure(),  # type: ignore[arg-type]
        retriever=_retriever(),
        config=WikiGenerationConfig(write_concurrency=1),
    )
    assert result.pages_planned == 3
    assert result.pages_written == 2
    assert result.pages_persisted == 2
    assert any("page_failed:architecture" in err for err in result.errors)


async def test_run_wiki_generation_unresolved_only_telemetry(
    db_session: AsyncSession,
) -> None:
    """A persistently-unverified `[[node:…]]` triggers T3's full repair
    budget; on exhaust, the gate strips the placeholder and ships at
    quality_status=degraded. The page persists; the run does NOT fail."""
    repo = await _make_repo(db_session)
    fake = FakeStructuredProvider()
    fake.queue(RepoOverview(one_line="x", long_description="y").model_dump_json())
    fake.queue(MindMap().model_dump_json())
    fake.queue(
        PagePlan.model_validate(
            {
                "pages": [
                    {"slug": "index", "title": "I", "purpose": "p1"},
                    {"slug": "architecture", "title": "A", "purpose": "p2"},
                    {"slug": "getting-started", "title": "G", "purpose": "p3"},
                ]
            }
        ).model_dump_json()
    )
    # Initial loop for index — emits an unverified citation (no
    # read_node_by_qn was called, so the ledger is empty).
    fake.queue_tool_turn(
        tool_uses=[
            (
                "write_page",
                {
                    "markdown": "# Index\n\nUnknown ref [[node:does.not.exist]] — but body is fine.",
                },
            )
        ]
    )
    fake.queue_tool_turn(text="")
    # Three repair attempts each re-emit the same bad citation so T3
    # exhausts the retry budget and falls back to stripping.
    for _ in range(_CITATION_GATE_MAX_REPAIRS):
        fake.queue_tool_turn(
            tool_uses=[
                (
                    "write_page",
                    {
                        "markdown": "# Index\n\nRepair still cites [[node:does.not.exist]].",
                    },
                )
            ]
        )
        fake.queue_tool_turn(text="")
    # Arch + Getting-started loops, no repair needed.
    fake.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": "# Arch\n\nFine body."})]
    )
    fake.queue_tool_turn(text="")
    fake.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": "# Getting started\n\nFine body."})]
    )
    fake.queue_tool_turn(text="")

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        # Serial writes so the queue order matches page order — keeps the
        # repair pass scoped to `index` instead of leaking onto siblings.
        config=WikiGenerationConfig(write_concurrency=1),
    )
    assert result.pages_persisted == 3
    # The bad placeholder was stripped to inline-code by T3's fallback,
    # so Stage 5 sees zero unresolved markers.
    assert result.unresolved_placeholders_total == 0


async def test_pipeline_does_not_raise_on_degraded_pages(
    db_session: AsyncSession,
) -> None:
    """A page that exhausts the citation-gate repair budget ships as
    `degraded` and is persisted. The run must not raise — quality issues
    are surfaced as warnings via `result.errors`, not aborts."""
    repo = await _make_repo(db_session)
    fake = FakeStructuredProvider()
    fake.queue(RepoOverview(one_line="x", long_description="y").model_dump_json())
    fake.queue(MindMap().model_dump_json())
    fake.queue(
        PagePlan.model_validate(
            {"pages": [{"slug": "index", "title": "I", "purpose": "p"}]}
        ).model_dump_json()
    )
    fake.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": "# Index\n\n[[node:missing.Symbol]]"})]
    )
    fake.queue_tool_turn(text="")
    for _ in range(_CITATION_GATE_MAX_REPAIRS):
        fake.queue_tool_turn(
            tool_uses=[
                ("write_page", {"markdown": "# Index\n\n[[node:missing.Symbol]]"})
            ]
        )
        fake.queue_tool_turn(text="")

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="abc",
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        config=WikiGenerationConfig(
            page_count_min=1,
            write_concurrency=1,
        ),
    )

    assert result.pages_persisted == 1
    assert any("page_quality:index:degraded" in err for err in result.errors)
    persisted = (
        (
            await db_session.execute(
                select(Document).where(
                    Document.repository_id == repo.id,
                    Document.doc_type == "wiki",
                )
            )
        )
        .scalars()
        .all()
    )
    assert [row.slug for row in persisted] == ["index"]


async def test_reindex_keeps_orphan_for_failed_pages(
    db_session: AsyncSession,
) -> None:
    """A slug whose Stage-4 page-write fails transiently must NOT have
    its previously persisted row orphan-deleted. The orchestrator passes
    `stages_1_4.page_failures` into `keep_slugs` for exactly this case."""
    repo = await _make_repo(db_session)
    # Pre-existing wiki row for the slug that will fail Stage 4 on this run.
    prior = Document(
        repository_id=repo.id,
        sync_run_id=None,
        slug="architecture",
        title="Architecture",
        doc_type="wiki",
        sort_order=1,
        parent_slug=None,
        source_commit="prev-commit",
        content="# Architecture\n\nprior body",
        content_hash="deadbeef",
        source_hash="deadbeef",
        model="prior-v1",
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        citations=[],
        quality={"quality_status": "ok"},
    )
    db_session.add(prior)
    await db_session.flush()

    class _IndexOkArchitectureFails:
        model = "mixed-v1"
        _idx = 0

        async def complete_text(self, *, system, blocks, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, *, system, blocks, schema, **_kwargs):
            if not hasattr(self, "_json_idx"):
                self._json_idx = 0
            self._json_idx += 1
            if self._json_idx == 1:
                return RepoOverview(one_line="x", long_description="y")
            if self._json_idx == 2:
                return MindMap()
            return PagePlan.model_validate(
                {
                    "pages": [
                        {"slug": "index", "title": "I", "purpose": "i"},
                        {"slug": "architecture", "title": "A", "purpose": "a"},
                    ]
                }
            )

        async def complete_with_tools(self, **_kwargs):
            from backend.app.wiki.llm_client import (
                StructuredCompletionError,
                ToolUseResult,
            )

            self._idx += 1
            # 1st call = index (ok), 2nd call = architecture (fail).
            if self._idx == 2:
                raise StructuredCompletionError("forced architecture failure")
            await _kwargs["tool_dispatch"](
                "write_page", {"markdown": f"# page-{self._idx}\n\nbody"}
            )
            return ToolUseResult(stop_reason="end_turn", turns_used=1)

    result = await run_wiki_generation(
        session=db_session,
        repository_id=repo.id,
        source_commit="new-commit",
        sync_run_id=None,
        llm=_IndexOkArchitectureFails(),  # type: ignore[arg-type]
        retriever=_retriever(),
        config=WikiGenerationConfig(write_concurrency=1, page_count_min=2),
    )

    assert any("page_failed:architecture" in err for err in result.errors)
    assert result.pages_persisted == 1
    rows = (
        (
            await db_session.execute(
                select(Document).where(
                    Document.repository_id == repo.id,
                    Document.doc_type == "wiki",
                )
            )
        )
        .scalars()
        .all()
    )
    by_slug = {row.slug: row for row in rows}
    # The transiently-failed page's prior row survives — not orphan-deleted.
    assert "architecture" in by_slug
    assert by_slug["architecture"].content == "# Architecture\n\nprior body"
    assert "index" in by_slug
