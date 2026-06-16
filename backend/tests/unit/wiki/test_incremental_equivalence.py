"""Equivalence matrix for the incremental wiki path.

Every scenario runs the same shape:

    repo A (incremental):  full(S0)  →  mutate M  →  incremental(M(S0))
    repo B (control):      seed S0   →  mutate M  →  full(M(S0))

and asserts `business_view(A) == business_view(B)` — the reader-visible
wiki must be indistinguishable from a full rebuild — plus that the
incremental run rewrote exactly the pages its dirty set justifies (the
scripted provider's queue is the LLM-call budget: an extra rewrite drains
it early, a missed rewrite leaves it undrained; both fail loudly).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.document import Document
from backend.app.wiki.incremental import load_artifact
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.retrieval import WikiRetrievalService
from backend.tests.unit.wiki.incremental_harness import (
    STANDARD_PAGES,
    DeterministicDbHybrid,
    ScriptedPage,
    ScriptedRepo,
    assert_drained,
    business_view,
    queue_full_run,
    run_full,
    run_incremental,
    run_pipeline,
    seed_standard,
)

pytestmark = pytest.mark.asyncio

Mutator = Callable[[AsyncSession, ScriptedRepo], Awaitable[None]]


async def _no_mutation(session: AsyncSession, repo_state: ScriptedRepo) -> None:
    del session, repo_state


async def _assert_equivalent(
    session: AsyncSession,
    *,
    mutate: Mutator,
    expected_dirty: set[str],
    pages: list[ScriptedPage] | None = None,
) -> None:
    pages = pages or STANDARD_PAGES
    repo_a = await ScriptedRepo.create(session, "wiki-eq-incremental")
    await seed_standard(session, repo_a)
    await run_full(session, repo_a, pages, source_commit="c1")
    await mutate(session, repo_a)
    await run_incremental(
        session,
        repo_a,
        pages,
        source_commit="c2",
        expected_dirty=expected_dirty,
    )

    repo_b = await ScriptedRepo.create(session, "wiki-eq-control")
    await seed_standard(session, repo_b)
    await mutate(session, repo_b)
    await run_full(session, repo_b, pages, source_commit="c2")

    assert await business_view(session, repo_a) == await business_view(session, repo_b)


# ---------------------------------------------------------------------------
# Scenarios 1-2: no change / new commit only
# ---------------------------------------------------------------------------


async def test_unchanged_repo_zero_rewrites(db_session: AsyncSession) -> None:
    """New commit, no content change: zero LLM calls (StrictProvider),
    every page clean, audit commit bumped — and still equivalent to a
    fresh full build at the new commit."""
    await _assert_equivalent(db_session, mutate=_no_mutation, expected_dirty=set())


async def test_unchanged_repo_bumps_source_commit(
    db_session: AsyncSession,
) -> None:
    repo = await ScriptedRepo.create(db_session, "wiki-eq-bump")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")
    result = await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty=set(),
    )
    assert result.pages_clean_skipped == len(STANDARD_PAGES)
    view = await business_view(db_session, repo)
    assert all(page["source_commit"] == "c2" for page in view.values())


# ---------------------------------------------------------------------------
# Scenarios 3-4: cited node changed / deleted
# ---------------------------------------------------------------------------


async def test_changed_cited_node_rewrites_one_page(
    db_session: AsyncSession,
) -> None:
    """Ingest recreates a changed node under a new UUID → the citing page
    (and index) rewrite; beta/gamma stay byte-identical."""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.change_node(
            session,
            "pkg.alpha_main",
            content="def alpha_main():\n    return 'alphaflow v2 rewritten'",
        )

    await _assert_equivalent(
        db_session, mutate=mutate, expected_dirty={"alpha", "index"}
    )


async def test_deleted_cited_node_rewrites_citing_page(
    db_session: AsyncSession,
) -> None:
    """Deleting a PRIVATE node leaves the public-api manifest (and hence the
    structural hash) intact — the citing page goes dirty via the dead UUID,
    not via re-plan."""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.delete_node(session, "pkg._alpha_helper")

    await _assert_equivalent(
        db_session, mutate=mutate, expected_dirty={"alpha", "index"}
    )


# ---------------------------------------------------------------------------
# Scenario 5: new file enters a page's top-k (no citation involved)
# ---------------------------------------------------------------------------


async def test_new_node_entering_topk_dirties_via_fingerprint(
    db_session: AsyncSession,
) -> None:
    """A new non-exported node (no manifest change → not structural) whose
    content matches beta's retrieval query enters beta's bundle. The page
    cites nothing new, but the evidence offer changed → retrieval_drift."""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.add_node(
            session,
            "pkg._beta_extra",
            content="def _beta_extra():\n    return 'betaflow extra detail'",
        )

    repo_a = await ScriptedRepo.create(db_session, "wiki-eq-topk-inc")
    await seed_standard(db_session, repo_a)
    await run_full(db_session, repo_a, STANDARD_PAGES, source_commit="c1")
    await mutate(db_session, repo_a)
    result = await run_incremental(
        db_session,
        repo_a,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty={"beta", "index"},
    )
    assert result.dirty_reasons["beta"] == "retrieval_drift"

    repo_b = await ScriptedRepo.create(db_session, "wiki-eq-topk-ctl")
    await seed_standard(db_session, repo_b)
    await mutate(db_session, repo_b)
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")
    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ---------------------------------------------------------------------------
# Scenario 6: repo-doc chunk changed
# ---------------------------------------------------------------------------


async def test_changed_doc_chunk_dirties_doc_evidence_page(
    db_session: AsyncSession,
) -> None:
    """gamma's bundle includes the guide chunk; recreating the chunk (new
    UUID, like ingest) shifts the fingerprint without touching structure."""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.change_doc_chunk(
            session,
            "docs/guide.md",
            0,
            "gammaflow guidebook: gammaflow internals walkthrough, rev2.",
        )

    await _assert_equivalent(
        db_session, mutate=mutate, expected_dirty={"gamma", "index"}
    )


# ---------------------------------------------------------------------------
# Scenario 7: spec change (plan-level edit) dirties only that page
# ---------------------------------------------------------------------------


async def test_spec_change_rewrites_only_that_page(
    db_session: AsyncSession,
) -> None:
    """Simulates a plan whose beta spec changed (steering edit / re-plan
    with one page retitled) by updating the persisted artifact plan. The
    spec_hash mismatch must rewrite beta alone (plus index).

    Mutates `title` — a contract field still in spec_hash — not `purpose`,
    which is now deliberately excluded (a reworded purpose alone no longer
    dirties a page; see spec_hash docstring + test_incremental_dirty)."""
    pages_v2 = [
        page
        if page.slug != "beta"
        else ScriptedPage(
            slug="beta",
            title="Beta (revised)",
            purpose="Documents the betaflow subsystem",
            cites=("pkg.beta_main",),
        )
        for page in STANDARD_PAGES
    ]

    repo_a = await ScriptedRepo.create(db_session, "wiki-eq-spec-inc")
    await seed_standard(db_session, repo_a)
    await run_full(db_session, repo_a, STANDARD_PAGES, source_commit="c1")
    # Surgically retitle ONE page inside the persisted (already normalised)
    # plan — replacing the whole payload with a freshly built plan would
    # shift normalisation defaults and dirty every spec.
    artifact = await load_artifact(db_session, repository_id=repo_a.id)
    assert artifact is not None
    plan_payload = dict(artifact.plan)
    plan_payload["pages"] = [
        {**page, "title": "Beta (revised)"}
        if page["slug"] == "beta"
        else page
        for page in plan_payload["pages"]
    ]
    artifact.plan = plan_payload
    await db_session.flush()

    result = await run_incremental(
        db_session,
        repo_a,
        pages_v2,
        source_commit="c2",
        expected_dirty={"beta", "index"},
    )
    assert result.dirty_reasons["beta"] == "spec_changed"

    repo_b = await ScriptedRepo.create(db_session, "wiki-eq-spec-ctl")
    await seed_standard(db_session, repo_b)
    await run_full(db_session, repo_b, pages_v2, source_commit="c2")
    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ---------------------------------------------------------------------------
# Scenario 8: structural change → re-plan, unchanged pages salvaged
# ---------------------------------------------------------------------------


async def test_structural_change_replans_but_salvages_unchanged_pages(
    db_session: AsyncSession,
) -> None:
    """Adding an exported symbol changes the public-api manifest → the
    structural hash moves → full re-plan. The new plan gains a delta page;
    alpha/beta/gamma specs and evidence are untouched, so only delta (new
    row) and index (sibling) are written."""
    pages_v2 = STANDARD_PAGES + [
        ScriptedPage(
            slug="delta",
            title="Delta",
            purpose="Documents the deltaflow subsystem",
            cites=("pkg.delta_main",),
        )
    ]

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.add_node(
            session,
            "pkg.delta_main",
            content="def delta_main():\n    return 'deltaflow v1'",
            summary="delta summary v1",
        )

    repo_a = await ScriptedRepo.create(db_session, "wiki-eq-struct-inc")
    await seed_standard(db_session, repo_a)
    await run_full(db_session, repo_a, STANDARD_PAGES, source_commit="c1")
    await mutate(db_session, repo_a)

    provider = FakeStructuredProvider()
    queue_full_run(provider, repo_a, pages_v2, write_slugs={"delta", "index"})
    result = await run_pipeline(db_session, repo_a, llm=provider, source_commit="c2")
    assert result.errors == []
    assert result.mode == "full"
    assert result.dirty_reasons == {
        "delta": "missing_row",
        "index": "sibling_dirty",
    }
    assert result.pages_clean_skipped == 3
    assert_drained(provider)

    repo_b = await ScriptedRepo.create(db_session, "wiki-eq-struct-ctl")
    await seed_standard(db_session, repo_b)
    await mutate(db_session, repo_b)
    await run_full(db_session, repo_b, pages_v2, source_commit="c2")
    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ---------------------------------------------------------------------------
# Scenario 9: schema version mismatch → full rebuild
# ---------------------------------------------------------------------------


async def test_schema_version_mismatch_forces_full_rebuild(
    db_session: AsyncSession,
) -> None:
    """Rows + artifact written by an older pipeline (simulated by zeroing
    their stamps): the artifact is not reusable and every page is dirty."""
    repo = await ScriptedRepo.create(db_session, "wiki-eq-schema")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    await db_session.execute(
        update(Document)
        .where(Document.repository_id == repo.id)
        .values(wiki_schema_version=0)
    )
    artifact = await load_artifact(db_session, repository_id=repo.id)
    assert artifact is not None
    artifact.wiki_schema_version = 0
    await db_session.flush()

    result = await run_full(db_session, repo, STANDARD_PAGES, source_commit="c2")
    assert result.mode == "full"
    assert set(result.dirty_reasons) == {p.slug for p in STANDARD_PAGES}
    assert all(
        reason in ("schema_version", "sibling_dirty")
        for reason in result.dirty_reasons.values()
    )


# ---------------------------------------------------------------------------
# Scenario 10: model change → artifact unusable → full rebuild
# ---------------------------------------------------------------------------


class _RenamedEmbed(FakeEmbedProvider):
    @property
    def model(self) -> str:
        return "fake-embed-v2"


async def test_embed_model_change_forces_full_rebuild(
    db_session: AsyncSession,
) -> None:
    repo = await ScriptedRepo.create(db_session, "wiki-eq-embed")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    retriever = WikiRetrievalService(
        hybrid=DeterministicDbHybrid(),  # type: ignore[arg-type]
        embedder=_RenamedEmbed(dims=8),
    )
    result = await run_full(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        retriever=retriever,
    )
    assert result.mode == "full"
    # Old fingerprints were stamped under fake-embed-v1 → every page drifts.
    assert set(result.dirty_reasons) == {p.slug for p in STANDARD_PAGES}
    assert all(
        reason in ("retrieval_drift", "sibling_dirty")
        for reason in result.dirty_reasons.values()
    )


async def test_chat_model_change_forces_full_rebuild(
    db_session: AsyncSession,
) -> None:
    repo = await ScriptedRepo.create(db_session, "wiki-eq-chat")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    provider = FakeStructuredProvider(model="fake-structured-v2")
    queue_full_run(provider, repo, STANDARD_PAGES)
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")
    assert result.errors == []
    # Artifact keyed on chat_model=v1 → not reusable → full re-plan. The
    # pages themselves carry no chat-model stamp, so the salvage pass may
    # still clear them — what matters is the plan was re-derived.
    assert result.mode == "full"
    assert provider.calls, "expected planning LLM calls after model change"


# ---------------------------------------------------------------------------
# Scenario 11: dirty-ratio threshold
# ---------------------------------------------------------------------------


async def test_dirty_at_exact_threshold_stays_incremental(
    db_session: AsyncSession,
) -> None:
    """2 dirty of 4 planned = 0.5 — NOT strictly above the 0.5 default →
    incremental mode survives. (Boundary contract: `>`.)"""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.change_node(
            session,
            "pkg.alpha_main",
            content="def alpha_main():\n    return 'alphaflow v2'",
        )

    await _assert_equivalent(
        db_session, mutate=mutate, expected_dirty={"alpha", "index"}
    )


async def test_dirty_above_threshold_falls_back_to_full(
    db_session: AsyncSession,
) -> None:
    """3 dirty of 4 = 0.75 > 0.5 → the run abandons the incremental plan,
    re-plans via LLM, and the salvage pass keeps gamma untouched."""

    async def mutate(session: AsyncSession, repo_state: ScriptedRepo) -> None:
        await repo_state.change_node(
            session,
            "pkg.alpha_main",
            content="def alpha_main():\n    return 'alphaflow v2'",
        )
        await repo_state.change_node(
            session,
            "pkg.beta_main",
            content="def beta_main():\n    return 'betaflow v2'",
        )

    repo_a = await ScriptedRepo.create(db_session, "wiki-eq-thresh-inc")
    await seed_standard(db_session, repo_a)
    await run_full(db_session, repo_a, STANDARD_PAGES, source_commit="c1")
    await mutate(db_session, repo_a)

    provider = FakeStructuredProvider()
    queue_full_run(
        provider,
        repo_a,
        STANDARD_PAGES,
        write_slugs={"alpha", "beta", "index"},
    )
    result = await run_pipeline(db_session, repo_a, llm=provider, source_commit="c2")
    assert result.errors == []
    assert result.mode == "full"
    assert set(result.dirty_reasons) == {"alpha", "beta", "index"}
    assert result.pages_clean_skipped == 1  # gamma salvaged
    assert_drained(provider)

    repo_b = await ScriptedRepo.create(db_session, "wiki-eq-thresh-ctl")
    await seed_standard(db_session, repo_b)
    await mutate(db_session, repo_b)
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")
    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ---------------------------------------------------------------------------
# Scenario 12: degraded page self-heals alone
# ---------------------------------------------------------------------------


async def test_degraded_page_rewritten_alone(db_session: AsyncSession) -> None:
    """A page stuck at quality_status=degraded (gate-exhausted on some past
    run) is dirty by definition and self-heals on the next sync without
    dragging clean siblings along."""
    repo = await ScriptedRepo.create(db_session, "wiki-eq-degraded")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    row: Any = (
        await db_session.execute(
            Document.__table__.select().where(
                Document.repository_id == repo.id, Document.slug == "alpha"
            )
        )
    ).one()
    quality = dict(row.quality or {})
    quality["quality_status"] = "degraded"
    await db_session.execute(
        update(Document)
        .where(Document.repository_id == repo.id, Document.slug == "alpha")
        .values(quality=quality)
    )

    result = await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty={"alpha", "index"},
    )
    assert result.dirty_reasons["alpha"] == "quality_degraded"
    view = await business_view(db_session, repo)
    assert view["alpha"]["quality_status"] == "ok"
