"""Cost-accounting tests for the incremental wiki path.

The budget tests pin down *which calls happen*; these pin down how the
spend is *booked*: every wiki stage records under its own label, an
incremental run books only the dirty pages' write traffic (and zero
planning), a clean run books nothing at all, and the processor stamps
the per-step rollup onto the `sync_jobs` row that the Jobs UI reads.

The `FakeStructuredProvider` reports synthetic token counts derived from
prompt/output text lengths — deterministic and non-zero, so "tokens
shrank because fewer pages were written" is a real comparison, not a
0 == 0 tautology.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.usage import (
    LlmUsageTally,
    StageUsage,
    llm_stage_var,
    rollup_stages,
)
from backend.app.models.enums import (
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.processor import RepoSyncProcessor
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.retrieval import WikiRetrievalService
from backend.tests.unit.wiki.incremental_harness import (
    FAKE_MODEL,
    STANDARD_PAGES,
    DeterministicDbHybrid,
    ScriptedRepo,
    StrictProvider,
    assert_drained,
    queue_full_run,
    queue_incremental_run,
    run_full,
    run_pipeline,
    seed_standard,
)

pytestmark = pytest.mark.asyncio

_WIKI_PLANNING_STAGES = {"wiki.analyze", "wiki.mindmap", "wiki.plan"}

# STANDARD_PAGES with one diagram page so a full run exercises wiki.diagram.
_DIAGRAM_PAGES = [
    replace(page, diagram=True) if page.slug == "beta" else page
    for page in STANDARD_PAGES
]


async def test_full_run_books_every_wiki_stage(db_session: AsyncSession) -> None:
    repo = await ScriptedRepo.create(db_session, "wiki-cost-full")
    await seed_standard(db_session, repo)

    tally = LlmUsageTally()
    provider = FakeStructuredProvider(usage_tally=tally)
    queue_full_run(provider, repo, _DIAGRAM_PAGES)
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c1")
    assert result.errors == []
    assert_drained(provider)

    expected = _WIKI_PLANNING_STAGES | {"wiki.write", "wiki.diagram"}
    assert expected <= set(tally.by_stage), (
        f"missing stages: {expected - set(tally.by_stage)}"
    )
    for label in expected:
        usage = tally.by_stage[label]
        assert usage.calls >= 1, f"{label}: no calls booked"
        assert usage.tokens_in > 0, f"{label}: zero input tokens"
        assert usage.tokens_out > 0, f"{label}: zero output tokens"
        assert usage.model == FAKE_MODEL


async def test_clean_rerun_books_zero_llm_usage(db_session: AsyncSession) -> None:
    """A no-change re-sync records not a single chat call in the tally.

    Uses an empty `FakeStructuredProvider` *with* a tally rather than
    `StrictProvider`: the claim under test is the accounting plane —
    nothing was recorded — on the same code path production runs.
    """
    repo = await ScriptedRepo.create(db_session, "wiki-cost-clean")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    tally = LlmUsageTally()
    provider = FakeStructuredProvider(usage_tally=tally)
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")

    assert result.errors == []
    assert result.mode == "incremental"
    assert result.pages_written == 0
    assert tally.by_stage == {}


async def test_partial_rerun_books_only_dirty_write_tokens(
    db_session: AsyncSession,
) -> None:
    """One changed node → the tally shows write-only traffic, strictly
    below the full run's write spend (2 of 4 pages rewritten)."""
    repo = await ScriptedRepo.create(db_session, "wiki-cost-partial")
    await seed_standard(db_session, repo)

    tally_full = LlmUsageTally()
    provider = FakeStructuredProvider(usage_tally=tally_full)
    queue_full_run(provider, repo, STANDARD_PAGES)
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c1")
    assert result.errors == []
    assert_drained(provider)

    await repo.change_node(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2'",
    )

    tally_inc = LlmUsageTally()
    provider = FakeStructuredProvider(usage_tally=tally_inc)
    queue_incremental_run(provider, repo, STANDARD_PAGES, dirty_slugs={"beta", "index"})
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")
    assert result.errors == []
    assert result.mode == "incremental"
    assert_drained(provider)

    assert set(tally_inc.by_stage) & _WIKI_PLANNING_STAGES == set()
    inc_write = tally_inc.by_stage["wiki.write"]
    full_write = tally_full.by_stage["wiki.write"]
    assert 0 < inc_write.calls < full_write.calls
    assert 0 < inc_write.tokens_out < full_write.tokens_out


class _StageSpyEmbed(FakeEmbedProvider):
    """Records the active stage label at every embed call."""

    def __init__(self) -> None:
        super().__init__(dims=8)
        self.stages: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.stages.append(llm_stage_var.get())
        return await super().embed(texts)


async def test_clean_run_recomputes_fingerprints_without_embedding(
    db_session: AsyncSession,
) -> None:
    """The cited fingerprint is retrieval-free: a clean re-sync recomputes
    every page's stamp straight from the DB (node content_hash + summary,
    doc-chunk content) and never calls the embedder. Under the old
    whole-bundle fingerprint the dirty check re-ran retrieval — an embed call
    per page on every push, even when nothing changed — so this is the spend
    the narrowing erases."""
    spy = _StageSpyEmbed()
    retriever = WikiRetrievalService(
        hybrid=DeterministicDbHybrid(),  # type: ignore[arg-type]
        embedder=spy,
    )
    repo = await ScriptedRepo.create(db_session, "wiki-cost-retrieval")
    await seed_standard(db_session, repo)
    await run_full(
        db_session, repo, STANDARD_PAGES, source_commit="c1", retriever=retriever
    )

    spy.stages.clear()
    result = await run_pipeline(
        db_session,
        repo,
        llm=StrictProvider(),
        source_commit="c2",
        retriever=retriever,
    )
    assert result.mode == "incremental"
    assert result.pages_written == 0
    assert spy.stages == [], (
        "clean dirty-check must not embed — the cited fingerprint reads the "
        f"DB, not retrieval (saw embed stages: {spy.stages})"
    )


# ---------------------------------------------------------------------------
# rollup_stages
# ---------------------------------------------------------------------------


async def test_rollup_prices_known_model_and_keeps_unknown_null() -> None:
    rollup = rollup_stages(
        {
            "wiki.write": StageUsage(
                calls=3, tokens_in=1_000_000, tokens_out=100_000, model="gpt-4o-mini"
            ),
            "wiki.retrieval": StageUsage(
                calls=4, tokens_in=1_000, tokens_out=0, model="fake-embed-v1"
            ),
        }
    )
    assert rollup is not None
    assert rollup.tokens_input == 1_001_000
    assert rollup.tokens_output == 100_000
    # gpt-4o-mini: 1M × $0.15/1M + 0.1M × $0.60/1M = $0.21 = 210_000 µUSD.
    # The unpriced fake embed model contributes tokens but no cost.
    assert rollup.cost_usd_micros == 210_000
    assert rollup.llm_model == "gpt-4o-mini"  # most tokens moved
    assert rollup.cost_breakdown["wiki.write"]["cost_usd_micros"] == 210_000
    assert rollup.cost_breakdown["wiki.retrieval"]["cost_usd_micros"] is None


async def test_rollup_all_unknown_models_yields_null_cost() -> None:
    rollup = rollup_stages(
        {
            "wiki.write": StageUsage(
                calls=2, tokens_in=500, tokens_out=200, model="fake-structured-v1"
            )
        }
    )
    assert rollup is not None
    assert rollup.tokens_input == 500
    assert rollup.tokens_output == 200
    assert rollup.cost_usd_micros is None
    assert rollup.llm_model == "fake-structured-v1"


async def test_rollup_empty_subset_is_none() -> None:
    assert rollup_stages({}) is None


async def test_rollup_bills_cached_tokens_at_cached_rate() -> None:
    # gpt-4o-mini: input $0.15/M, cached input $0.075/M. 1M input with
    # 600k cached = 400k×0.15 + 600k×0.075 = $0.105; + 0.1M out × $0.60
    # = $0.06 → $0.165 = 165_000 µUSD. The flat-rate figure would be
    # 210_000 — the difference is exactly what dropping the cached
    # count used to overstate.
    rollup = rollup_stages(
        {
            "wiki.write": StageUsage(
                calls=3,
                tokens_in=1_000_000,
                tokens_out=100_000,
                tokens_cached=600_000,
                model="gpt-4o-mini",
            ),
        }
    )
    assert rollup is not None
    assert rollup.tokens_cached == 600_000
    assert rollup.cost_usd_micros == 165_000
    assert rollup.cost_breakdown["wiki.write"]["tokens_cached"] == 600_000


# ---------------------------------------------------------------------------
# Processor stamping
# ---------------------------------------------------------------------------


async def _make_job(db_session: AsyncSession, step: SyncStep) -> SyncJob:
    batch = SyncBatch(
        kind=SyncBatchKind.REPO_SYNC,
        trigger=SyncBatchTrigger.MANUAL,
        label="acme/demo",
        status=SyncJobStatus.RUNNING,
    )
    db_session.add(batch)
    await db_session.flush()
    job = SyncJob(
        batch_id=batch.id,
        step=step,
        title=step.value,
        status=SyncJobStatus.RUNNING,
    )
    db_session.add(job)
    await db_session.flush()
    return job


async def test_complete_step_stamps_wiki_usage_columns(
    db_session: AsyncSession,
) -> None:
    tally = LlmUsageTally()
    tally.record(
        stage="wiki.write",
        model="gpt-4o-mini",
        tokens_in=200_000,
        tokens_out=50_000,
        tokens_cached=100_000,
        calls=12,
    )
    tally.record(
        stage="wiki.retrieval",
        model="text-embedding-3-small",
        tokens_in=1_000,
    )
    # Foreign stage must NOT leak into the wiki step's rollup.
    tally.record(stage="embed.code", model="text-embedding-3-small", tokens_in=999)

    processor = RepoSyncProcessor(usage_tally=tally)
    job = await _make_job(db_session, SyncStep.GENERATE_WIKI)
    await processor._complete_step(db_session, job, progress=100)

    assert job.tokens_input == 201_000
    assert job.tokens_output == 50_000
    assert job.tokens_cached == 100_000
    # wiki.write: 100k uncached × $0.15/M + 100k cached × $0.075/M
    # + 0.05M out × $0.60/M = $0.0525 = 52_500 µUSD;
    # wiki.retrieval: 1_000 × $0.02/1M = $0.00002 = 20 µUSD.
    assert job.cost_usd_micros == 52_520
    assert job.llm_model == "gpt-4o-mini"
    assert set(job.cost_breakdown) == {"wiki.write", "wiki.retrieval"}


async def test_complete_step_leaves_non_llm_steps_null(
    db_session: AsyncSession,
) -> None:
    tally = LlmUsageTally()
    tally.record(stage="wiki.write", model="gpt-4o-mini", tokens_in=10, tokens_out=5)

    processor = RepoSyncProcessor(usage_tally=tally)
    job = await _make_job(db_session, SyncStep.CLONE)
    await processor._complete_step(db_session, job, progress=100)

    assert job.tokens_input is None
    assert job.tokens_output is None
    assert job.cost_usd_micros is None
    assert job.llm_model is None
    assert job.cost_breakdown is None


async def test_complete_step_skips_steps_with_no_recorded_stages(
    db_session: AsyncSession,
) -> None:
    """An LLM-owning step whose stages never fired (e.g. zero dirty wiki
    pages with a fake embedder) keeps NULL columns — "nothing recorded",
    not a fake $0."""
    processor = RepoSyncProcessor(usage_tally=LlmUsageTally())
    job = await _make_job(db_session, SyncStep.GENERATE_WIKI)
    await processor._complete_step(db_session, job, progress=100)

    assert job.tokens_input is None
    assert job.cost_usd_micros is None
    assert job.cost_breakdown is None
