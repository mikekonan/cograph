"""Spec carry-over tests — the fix for the planner-jitter rewrite storm.

A full-mode re-plan regenerates the page plan with a non-deterministic LLM,
so an unchanged page's *contract* (title / covers_questions / tree position)
gets reworded run-to-run. That moved `spec_hash` and fired `spec_changed`,
rewriting pages whose code never changed — the dominant steady-state wiki
cost once retrieval-jitter was fixed.

`compute_dirty_slugs(prior_specs=...)` pins every page that survives from the
prior plan to that prior contract for the spec-drift gate: only its cited
evidence can dirty it. These tests prove both directions —

  * a reworded contract with UNCHANGED evidence no longer rewrites the page
    (and the prior spec is returned for the caller to persist), and
  * a real evidence change STILL rewrites it, carry-over or not (the
    never-underspend safety property must survive the new path).
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.wiki.incremental import (
    compute_dirty_slugs,
    load_artifact,
    load_page_records,
    rehydrate_artifact,
    spec_hash,
)
from backend.tests.unit.wiki.incremental_harness import (
    STANDARD_PAGES,
    ScriptedRepo,
    plan_for,
    run_full,
    seed_standard,
)

pytestmark = pytest.mark.asyncio


def _drifted_plan():
    """The prior plan with `alpha`'s title reworded — same slug, cites and
    purpose, so ONLY the contract (spec_hash) moves, not the evidence."""
    pages = [
        dataclasses.replace(page, title="Alpha (rephrased by the planner)")
        if page.slug == "alpha"
        else page
        for page in STANDARD_PAGES
    ]
    return plan_for(pages)


async def _prior_specs(session: AsyncSession, repo: ScriptedRepo):
    artifact = await load_artifact(session, repository_id=repo.id)
    assert artifact is not None
    arts = rehydrate_artifact(artifact)
    assert arts is not None
    return {spec.slug: spec for spec in arts.plan.pages}


async def test_reworded_contract_without_carryover_is_spec_changed(
    db_session: AsyncSession,
) -> None:
    """Baseline: with no `prior_specs`, a reworded title dirties the page as
    `spec_changed` and drags `index` in as its sibling — the exact storm."""
    repo = await ScriptedRepo.create(db_session, "carryover-baseline")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    records = await load_page_records(db_session, repository_id=repo.id)
    report = await compute_dirty_slugs(
        db_session,
        repository_id=repo.id,
        plan=_drifted_plan(),
        records=records,
    )

    assert report.dirty.get("alpha") == "spec_changed"
    assert report.dirty.get("index") == "sibling_dirty"
    assert report.reconciled_specs == {}


async def test_reworded_contract_with_carryover_stays_clean(
    db_session: AsyncSession,
) -> None:
    """The fix: pinning to the prior contract, a reworded title with unchanged
    evidence rewrites NOTHING, and the prior spec is offered for persistence."""
    repo = await ScriptedRepo.create(db_session, "carryover-clean")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    prior = await _prior_specs(db_session, repo)
    records = await load_page_records(db_session, repository_id=repo.id)
    report = await compute_dirty_slugs(
        db_session,
        repository_id=repo.id,
        plan=_drifted_plan(),
        records=records,
        prior_specs=prior,
    )

    assert report.dirty == {}, f"carry-over should rewrite nothing: {report.dirty}"
    # The reused page carries its PRIOR contract, not the reworded one, and its
    # persisted spec_hash matches the stored stamp (keeps the next sync clean).
    assert "alpha" in report.reconciled_specs
    assert report.reconciled_specs["alpha"].title == "Alpha"
    assert spec_hash(report.reconciled_specs["alpha"]) == records["alpha"].spec_hash


async def test_carryover_never_masks_a_real_evidence_change(
    db_session: AsyncSession,
) -> None:
    """Safety: carry-over pins only the SPEC axis. A cited node that actually
    moved must still land the page in the rewrite set — never underspend."""
    repo = await ScriptedRepo.create(db_session, "carryover-safety")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    # Real change to alpha's cited evidence (ingest semantics: new UUID).
    await repo.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2 CHANGED'",
    )

    prior = await _prior_specs(db_session, repo)
    records = await load_page_records(db_session, repository_id=repo.id)
    report = await compute_dirty_slugs(
        db_session,
        repository_id=repo.id,
        plan=_drifted_plan(),  # contract ALSO reworded — carry-over active
        records=records,
        prior_specs=prior,
    )

    assert "alpha" in report.dirty, (
        f"evidence change must dirty the page despite carry-over: {report.dirty}"
    )
    assert "alpha" not in report.reconciled_specs
