"""LLM call-budget tests for the incremental wiki path.

The equivalence matrix proves the OUTPUT matches a full rebuild; these
tests pin down the SPEND: an incremental run may make exactly the calls
its dirty set justifies — no planning calls, no clean-page writes, no
clean-page diagrams — while keeping the full repair budget available to
the pages it does rewrite.

The scripted `FakeStructuredProvider` queue doubles as the budget meter
(an unexpected call drains it early and errors; an unconsumed entry fails
`assert_drained`), and `StrictProvider` hard-fails the zero-call case —
`FakeStructuredProvider.complete_with_tools` silently degrades on an
empty queue, so an empty fake cannot prove zero calls.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.tests.unit.wiki.incremental_harness import (
    MERMAID_BLOCK,
    STANDARD_PAGES,
    ScriptedPage,
    ScriptedRepo,
    StrictProvider,
    assert_drained,
    business_view,
    page_body,
    run_full,
    run_incremental,
    run_pipeline,
    seed_standard,
)

pytestmark = pytest.mark.asyncio


async def test_clean_repo_makes_zero_wiki_llm_calls(
    db_session: AsyncSession,
) -> None:
    """Re-sync of an unchanged repo: not a single text/json/tool call."""
    repo = await ScriptedRepo.create(db_session, "wiki-budget-zero")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    result = await run_pipeline(
        db_session, repo, llm=StrictProvider(), source_commit="c2"
    )
    assert result.errors == []
    assert result.mode == "incremental"
    assert result.pages_written == 0
    assert result.pages_clean_skipped == len(STANDARD_PAGES)


async def test_single_dirty_page_spends_no_planning_calls(
    db_session: AsyncSession,
) -> None:
    """One changed node → only the citing page's (and index's) agent turns
    run. Zero complete_text/complete_json traffic means the overview,
    mindmap, and plan stages were all reused from the artifact."""
    repo = await ScriptedRepo.create(db_session, "wiki-budget-one")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")
    await repo.change_node(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2'",
    )

    result = await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty={"beta", "index"},
    )
    assert result.pages_written == 2


async def test_repair_budget_available_while_clean_pages_stay_silent(
    db_session: AsyncSession,
) -> None:
    """A dirty page whose first draft cites an unverified node gets its
    full T3 repair pass (a second agent loop) — incrementality must not
    starve quality gates — while clean pages still cost zero calls."""
    repo = await ScriptedRepo.create(db_session, "wiki-budget-repair")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")
    await repo.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2'",
    )

    alpha = next(p for p in STANDARD_PAGES if p.slug == "alpha")
    index = next(p for p in STANDARD_PAGES if p.slug == "index")
    provider = FakeStructuredProvider()
    # Plan order: index first (sibling_dirty), clean write.
    provider.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": page_body(repo, index)})]
    )
    provider.queue_tool_turn(text="")
    # Alpha, first loop: cites a node never read → not in the evidence
    # ledger → T3 fires a repair.
    provider.queue_tool_turn(
        tool_uses=[
            (
                "write_page",
                {"markdown": "# Alpha\n\nBroken cite [[node:pkg.ghost]]."},
            )
        ]
    )
    provider.queue_tool_turn(text="")
    # Alpha, repair loop: verified citations.
    provider.queue_tool_turn(
        tool_uses=[
            ("read_node_by_qn", {"qualified_name": "pkg.alpha_main"}),
            ("read_node_by_qn", {"qualified_name": "pkg._alpha_helper"}),
            ("write_page", {"markdown": page_body(repo, alpha)}),
        ]
    )
    provider.queue_tool_turn(text="")

    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")
    assert result.errors == []
    assert result.mode == "incremental"
    assert set(result.dirty_reasons) == {"alpha", "index"}
    assert_drained(provider)

    view = await business_view(db_session, repo)
    assert view["alpha"]["quality_status"] == "ok"
    row = (
        await db_session.execute(
            select(Document).where(
                Document.repository_id == repo.id, Document.slug == "alpha"
            )
        )
    ).scalar_one()
    assert row.quality["repair_attempts"] == 1


async def test_diagrams_synthesized_only_for_dirty_pages(
    db_session: AsyncSession,
) -> None:
    """Two diagram pages; one goes dirty. Exactly one Stage 4b call is
    queued — a second one (for the clean page) would leave the queue
    undrained or drain it early. The clean page keeps its old diagram."""
    pages = [
        STANDARD_PAGES[0],
        ScriptedPage(
            slug="alpha",
            title="Alpha",
            purpose="Documents the alphaflow subsystem",
            cites=("pkg.alpha_main", "pkg._alpha_helper"),
            diagram=True,
        ),
        ScriptedPage(
            slug="beta",
            title="Beta",
            purpose="Documents the betaflow subsystem",
            cites=("pkg.beta_main",),
            diagram=True,
        ),
        STANDARD_PAGES[3],
    ]
    repo = await ScriptedRepo.create(db_session, "wiki-budget-diagram")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, pages, source_commit="c1")
    await repo.change_node(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2'",
    )

    await run_incremental(
        db_session,
        repo,
        pages,
        source_commit="c2",
        expected_dirty={"beta", "index"},
    )
    view = await business_view(db_session, repo)
    assert MERMAID_BLOCK.splitlines()[1] in view["alpha"]["content"]
    assert MERMAID_BLOCK.splitlines()[1] in view["beta"]["content"]
