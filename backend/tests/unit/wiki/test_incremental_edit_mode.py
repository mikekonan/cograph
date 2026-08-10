"""Cheap edit path for genuinely-minor changes.

Two layers:

1. Pure unit tests over the dispatch helpers (`_edit_eligible`, the churn
   inputs, ledger seeding, streak accounting) — no DB, no provider.

2. DB-backed equivalence/behaviour tests over the real pipeline with edit
   mode ON. The claim mirrors the write-path matrix: an edited page must be
   byte-indistinguishable from a full rebuild, the edit path must fire only
   when the contract is unchanged and the delta is small, and a delta over
   the churn budget (or a streak at the cap) must escalate to a full write.

`harness_config` defaults edit mode OFF; every run here opts in via
`run_incremental_edits` (or an explicit config), so the existing write-path
matrix in `test_incremental_equivalence.py` is unaffected.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.incremental import PageRecord
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    _edit_eligible,
    _parse_cited_refs,
    _seed_edit_ledger,
    _strip_trailing_mermaid,
)
from backend.app.wiki.retrieval import CodeChunk, DocChunk, PageBundle
from backend.app.wiki.schemas import QualityStatus
from backend.app.wiki.store import _next_edit_streak
from backend.app.wiki.version import WIKI_SCHEMA_VERSION
from backend.tests.unit.wiki.incremental_harness import (
    STANDARD_PAGES,
    FakeStructuredProvider,
    ScriptedPage,
    ScriptedRepo,
    business_view,
    harness_config,
    page_body,
    queue_page_turns,
    run_full,
    run_incremental_edits,
    run_pipeline,
    seed_standard,
)

# Plan-order lookup so escalation tests can hand-build a provider queue that
# matches the serial (write_concurrency=1) dispatch order.
_PAGE_BY_SLUG = {page.slug: page for page in STANDARD_PAGES}

# `asyncio_mode = "auto"` (backend/pyproject.toml) runs the async DB-backed
# tests as coroutines automatically; the pure helpers below stay synchronous.
# No module-level `pytest.mark.asyncio` — it would wrap the sync tests too.


# ===========================================================================
# Pure helpers — no DB, no provider
# ===========================================================================


def _record(**over: object) -> PageRecord:
    """An edit-eligible PageRecord; override one field per case."""
    base: dict[str, object] = {
        "slug": "alpha",
        "spec_hash": "spec",
        "wiki_schema_version": WIKI_SCHEMA_VERSION,
        "source_node_ids": ("11111111-1111-1111-1111-111111111111",),
        "source_repo_doc_chunk_ids": (),
        "quality_status": QualityStatus.OK,
        "cited_content_hashes": {"11111111-1111-1111-1111-111111111111": "h1"},
        "content_src": "# Alpha\n\nThe symbol [[node:pkg.alpha_main]] does work.",
        "edit_streak": 0,
    }
    base.update(over)
    return PageRecord(**base)  # type: ignore[arg-type]


def _cfg(**over: object) -> WikiGenerationConfig:
    return WikiGenerationConfig(enable_edit_mode=True, **over)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("cited_node_content_changed", True),
        ("cited_node_missing", True),
        ("cited_chunk_missing", True),
        ("cited_evidence_changed", True),
        # Contract / structural / quality reasons never edit.
        ("spec_changed", False),
        ("schema_version", False),
        ("quality_degraded", False),
        ("missing_row", False),
        ("sibling_dirty", False),
        (None, False),
    ],
)
def test_edit_eligible_by_reason(reason: str | None, expected: bool) -> None:
    assert (
        _edit_eligible(reason=reason, record=_record(), cfg=_cfg(), is_index=False)
        is expected
    )


def test_edit_eligible_requires_content_src() -> None:
    assert not _edit_eligible(
        reason="cited_evidence_changed",
        record=_record(content_src=None),
        cfg=_cfg(),
        is_index=False,
    )


def test_edit_eligible_requires_current_schema() -> None:
    assert not _edit_eligible(
        reason="cited_evidence_changed",
        record=_record(wiki_schema_version=WIKI_SCHEMA_VERSION - 1),
        cfg=_cfg(),
        is_index=False,
    )


def test_edit_eligible_never_edits_index() -> None:
    """The index narrates the whole wiki (ToC, reading order) — full-write
    keeps its cross-links honest when a sibling moves."""
    assert not _edit_eligible(
        reason="cited_evidence_changed",
        record=_record(slug="index"),
        cfg=_cfg(),
        is_index=True,
    )


def test_edit_eligible_respects_streak_cap() -> None:
    cfg = _cfg(edit_streak_cap=3)
    assert _edit_eligible(
        reason="cited_evidence_changed", record=_record(edit_streak=2), cfg=cfg, is_index=False
    )
    assert not _edit_eligible(
        reason="cited_evidence_changed", record=_record(edit_streak=3), cfg=cfg, is_index=False
    )


def test_edit_eligible_off_when_disabled() -> None:
    assert not _edit_eligible(
        reason="cited_evidence_changed",
        record=_record(),
        cfg=WikiGenerationConfig(enable_edit_mode=False),
        is_index=False,
    )


def test_edit_eligible_missing_record() -> None:
    assert not _edit_eligible(
        reason="cited_evidence_changed", record=None, cfg=_cfg(), is_index=False
    )


def test_strip_trailing_mermaid() -> None:
    body = "# Title\n\nProse.\n\n```mermaid\nflowchart LR\n  a --> b\n```\n"
    assert _strip_trailing_mermaid(body) == "# Title\n\nProse."


def test_strip_trailing_mermaid_no_fence_is_noop() -> None:
    body = "# Title\n\nProse with a `code` span."
    assert _strip_trailing_mermaid(body) == body


def test_strip_trailing_mermaid_keeps_non_trailing_fence() -> None:
    """Only a *trailing* diagram is stripped; an inline example fence stays."""
    body = "# T\n\n```mermaid\ngraph\n```\n\nMore prose after the diagram."
    assert _strip_trailing_mermaid(body) == body


def test_parse_cited_refs() -> None:
    src = (
        "# Page\n"
        "See [[node:pkg.alpha_main]] and [[node:pkg.Beta.run]].\n"
        "Docs: [[doc:docs/guide.md#setup]] and [[doc:./README.md]].\n"
        "Empty [[node:]] is ignored."
    )
    nodes, docs = _parse_cited_refs(src)
    assert nodes == {"pkg.alpha_main", "pkg.Beta.run"}
    assert docs == {"docs/guide.md", "README.md"}


def test_parse_cited_refs_empty() -> None:
    assert _parse_cited_refs(None) == (set(), set())
    assert _parse_cited_refs("no citations here") == (set(), set())


def test_seed_edit_ledger_unions_bundle_and_survivors() -> None:
    bundle = PageBundle(
        code_chunks=[
            CodeChunk(
                qualified_name="pkg.alpha_main",
                file_path="src/alpha.py",
                start_line=1,
                end_line=4,
                language="python",
                snippet="def alpha_main(): ...",
                code_node_id=uuid4(),
            )
        ],
        doc_chunks=[
            DocChunk(
                file_path="docs/guide.md",
                chunk_index=0,
                snippet="## Guide",
                chunk_id=uuid4(),
            )
        ],
    )
    ledger = _seed_edit_ledger(
        bundle=bundle,
        surviving_node_qns=["pkg._alpha_helper", "pkg.alpha_main"],
        surviving_doc_paths=["docs/legacy.md"],
    )
    assert ledger.verified_node_qns == {"pkg.alpha_main", "pkg._alpha_helper"}
    assert ledger.verified_doc_paths == {"docs/guide.md", "docs/legacy.md"}


def test_seed_edit_ledger_dedups_by_record_id() -> None:
    node_id = uuid4()
    bundle = PageBundle(
        code_chunks=[
            CodeChunk(
                qualified_name="pkg.alpha_main",
                file_path="src/alpha.py",
                start_line=1,
                end_line=4,
                language="python",
                snippet="def alpha_main(): ...",
                code_node_id=node_id,
            )
        ]
    )
    ledger = _seed_edit_ledger(
        bundle=bundle,
        surviving_node_qns=["pkg.alpha_main"],  # same qn as the bundle chunk
        surviving_doc_paths=[],
    )
    assert len(ledger) == 1


@pytest.mark.parametrize(
    "mode,prev,expected",
    [
        ("write", 0, 0),
        ("write", 5, 0),  # a full write resets the streak
        ("edit", 0, 1),
        ("edit", 2, 3),
    ],
)
def test_next_edit_streak(mode: str, prev: int, expected: int) -> None:
    existing = SimpleNamespace(edit_streak=prev)
    assert _next_edit_streak(mode=mode, existing=existing) == expected


def test_next_edit_streak_new_row() -> None:
    assert _next_edit_streak(mode="write", existing=None) == 0
    assert _next_edit_streak(mode="edit", existing=None) == 1


# ===========================================================================
# DB-backed: edit path produces full-rebuild-equivalent output
# ===========================================================================


async def _seeded_full(session: AsyncSession, name: str) -> ScriptedRepo:
    repo = await ScriptedRepo.create(session, name)
    await seed_standard(session, repo)
    await run_full(session, repo, STANDARD_PAGES, source_commit="c1")
    return repo


async def test_summary_regen_edits_page_equivalent(db_session: AsyncSession) -> None:
    """A neighbour-driven summary regen (node UUID alive, content unchanged)
    changes the page's cited evidence (the node summary is hashed into the
    fingerprint) → `cited_evidence_changed`, churn 0 → cheap edit. Output
    must equal a full rebuild."""
    repo_a = await _seeded_full(db_session, "edit-summary-inc")
    await repo_a.change_node_summary(db_session, "pkg.alpha_main", "alpha summary v2")
    result = await run_incremental_edits(
        db_session,
        repo_a,
        STANDARD_PAGES,
        source_commit="c2",
        edit_slugs={"alpha"},
        write_slugs={"index"},
    )
    assert result.dirty_reasons["alpha"] == "cited_evidence_changed"

    repo_b = await ScriptedRepo.create(db_session, "edit-summary-ctl")
    await seed_standard(db_session, repo_b)
    await repo_b.change_node_summary(db_session, "pkg.alpha_main", "alpha summary v2")
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")

    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


async def test_content_change_within_churn_edits_equivalent(
    db_session: AsyncSession,
) -> None:
    """alpha cites two nodes; changing one is churn 0.5 (== budget) → edit.
    The edited body carries the new content digest and equals a full
    rebuild."""
    repo_a = await _seeded_full(db_session, "edit-churn-inc")
    v1 = await business_view(db_session, repo_a)
    await repo_a.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2 edited'",
    )
    result = await run_incremental_edits(
        db_session,
        repo_a,
        STANDARD_PAGES,
        source_commit="c2",
        edit_slugs={"alpha"},
        write_slugs={"index"},
    )
    assert result.dirty_reasons["alpha"] == "cited_node_missing"
    # Freshness (guards against a tautology): the edit rewrote alpha against the
    # NEW node body, so its persisted content must differ from the v1 it started
    # from — a stale echo of the old body would leave this equal.
    v2 = await business_view(db_session, repo_a)
    assert v2["alpha"]["content"] != v1["alpha"]["content"]

    repo_b = await ScriptedRepo.create(db_session, "edit-churn-ctl")
    await seed_standard(db_session, repo_b)
    await repo_b.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2 edited'",
    )
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")

    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


async def test_content_change_over_churn_escalates(db_session: AsyncSession) -> None:
    """beta cites one node; changing it is churn 1.0 > budget → the edit
    gate escalates to a full write. `pages_edited` stays 0 and the result
    still equals a full rebuild."""
    repo_a = await _seeded_full(db_session, "edit-escalate-inc")
    await repo_a.change_node(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2 wholesale rewrite'",
    )
    await run_incremental_edits(
        db_session,
        repo_a,
        STANDARD_PAGES,
        source_commit="c2",
        edit_slugs=set(),
        write_slugs={"beta", "index"},
    )

    repo_b = await ScriptedRepo.create(db_session, "edit-escalate-ctl")
    await seed_standard(db_session, repo_b)
    await repo_b.change_node(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2 wholesale rewrite'",
    )
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")

    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ===========================================================================
# DB-backed: escalation guards (an edit never ships stale or invalid content)
# ===========================================================================


async def test_changed_symbol_absent_from_bundle_escalates(
    db_session: AsyncSession,
) -> None:
    """A cited symbol whose body changed IN PLACE (same UUID) but which fell
    out of the page's retrieval top-k is absent from the tool-less editor's
    only window onto current code. The edit MUST escalate to a full write
    rather than keep stale prose behind a still-valid citation.

    Discriminator: an editor body is queued but goes UNCONSUMED — the
    escalation fires before the editor LLM call. Without the guard the body is
    consumed and `pages_edited` is 1."""
    repo = await _seeded_full(db_session, "edit-stale-guard")
    # _alpha_helper's body moves to tokens that DON'T overlap alpha's query, so
    # it drops out of alpha's top-k; alpha_main keeps "alphaflow" and stays in.
    await repo.change_node_body(
        db_session, "pkg._alpha_helper", content="def zzz():\n    return 'qqqq wwww'"
    )
    alpha_body = page_body(repo, _PAGE_BY_SLUG["alpha"])
    provider = FakeStructuredProvider()
    # Serial dispatch is plan order: index, then alpha. Queue tool turns in that
    # order for the index write + the escalated alpha write; queue ONE editor
    # body that a correct run must never pop.
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["index"])
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["alpha"])
    provider.queue(alpha_body)
    result = await run_pipeline(
        db_session,
        repo,
        llm=provider,
        source_commit="c2",
        config=harness_config(enable_edit_mode=True),
    )
    assert result.errors == []
    assert result.mode == "incremental"
    assert result.dirty_reasons["alpha"] == "cited_node_content_changed"
    assert result.pages_edited == 0
    assert provider._responses == [alpha_body], (
        "editor complete_text was called — the absent-from-bundle guard misfired"
    )
    assert provider._tool_turns == []

    repo_b = await ScriptedRepo.create(db_session, "edit-stale-ctl")
    await seed_standard(db_session, repo_b)
    await repo_b.change_node_body(
        db_session, "pkg._alpha_helper", content="def zzz():\n    return 'qqqq wwww'"
    )
    await run_full(db_session, repo_b, STANDARD_PAGES, source_commit="c2")
    assert await business_view(db_session, repo) == await business_view(
        db_session, repo_b
    )


async def test_edit_with_invalid_citation_escalates(db_session: AsyncSession) -> None:
    """The edit path has NO agentic citation repair: a body citing a symbol
    absent from the seeded ledger fails the citation gate and escalates to the
    full write, so a dangling citation can never ship from an edit."""
    repo = await _seeded_full(db_session, "edit-bad-cite")
    await repo.change_node_summary(db_session, "pkg.alpha_main", "alpha summary v2")
    provider = FakeStructuredProvider()
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["index"])
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["alpha"])  # the escalated write
    # Editor returns a body citing a node outside bundle ∪ survivors.
    provider.queue("# Alpha\n\nSee [[node:pkg.ghost]] — not in the evidence ledger.")
    result = await run_pipeline(
        db_session,
        repo,
        llm=provider,
        source_commit="c2",
        config=harness_config(enable_edit_mode=True),
    )
    assert result.errors == []
    assert result.mode == "incremental"
    assert result.pages_edited == 0  # bad citation → escalated, not shipped as edit
    assert provider._responses == []  # editor body WAS consumed (gate runs post-LLM)
    assert provider._tool_turns == []  # the escalated write consumed its turns
    # The shipped alpha cites only real symbols (it came from the full write).
    view = await business_view(db_session, repo)
    assert "missing" not in view["alpha"]["source_nodes"]
    assert "pkg.ghost" not in view["alpha"]["content"]


async def test_edit_without_cited_hash_snapshot_escalates(
    db_session: AsyncSession,
) -> None:
    """A row that cites symbols but carries an empty cited-hash snapshot can't
    be diffed against the live graph, so the symbol delta can't be scoped — the
    edit escalates rather than guess. (Post-0063 writes always snapshot; this
    guards an anomalous citations-without-map row.)"""
    repo = await _seeded_full(db_session, "edit-no-snapshot")
    row = await _alpha_row(db_session, repo)
    row.cited_content_hashes = {}
    await db_session.flush()
    await repo.change_node_summary(db_session, "pkg.alpha_main", "alpha summary v2")
    alpha_body = page_body(repo, _PAGE_BY_SLUG["alpha"])
    provider = FakeStructuredProvider()
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["index"])
    queue_page_turns(provider, repo, _PAGE_BY_SLUG["alpha"])
    provider.queue(alpha_body)
    result = await run_pipeline(
        db_session,
        repo,
        llm=provider,
        source_commit="c2",
        config=harness_config(enable_edit_mode=True),
    )
    assert result.errors == []
    assert result.pages_edited == 0
    assert provider._responses == [alpha_body]  # escalated before the editor call
    assert provider._tool_turns == []


async def test_shared_node_fans_out_edit_and_escalate(
    db_session: AsyncSession,
) -> None:
    """Two pages cite pkg.alpha_main; an in-place body change dirties BOTH
    (cited_node_content_changed). Each picks its path by its OWN churn: alpha
    (2 cites → 0.5) edits; the single-cite page (1 cite → 1.0) escalates. The
    node stays in both top-k sets, so the absent-from-bundle guard stays out of
    the way. Both equal a full rebuild."""
    pages = [
        *STANDARD_PAGES,
        ScriptedPage(
            slug="solo",
            title="Solo",
            purpose="Documents the alphaflow entrypoint only",
            cites=("pkg.alpha_main",),
        ),
    ]
    repo_a = await ScriptedRepo.create(db_session, "edit-shared-inc")
    await seed_standard(db_session, repo_a)
    await run_full(db_session, repo_a, pages, source_commit="c1")
    await repo_a.change_node_body(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2 same topk'",
    )
    # In-place body edits keep stable node ids → zero coverage collapse, so the
    # run stays incremental however many pages are dirty (no dirty-volume
    # backstop to fight anymore). This test is about per-page edit-vs-escalate
    # fan-out, which is unaffected.
    result = await run_incremental_edits(
        db_session,
        repo_a,
        pages,
        source_commit="c2",
        edit_slugs={"alpha"},
        write_slugs={"solo", "index"},
        config=harness_config(enable_edit_mode=True),
    )
    assert result.dirty_reasons["alpha"] == "cited_node_content_changed"
    assert result.dirty_reasons["solo"] == "cited_node_content_changed"

    repo_b = await ScriptedRepo.create(db_session, "edit-shared-ctl")
    await seed_standard(db_session, repo_b)
    await repo_b.change_node_body(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2 same topk'",
    )
    await run_full(db_session, repo_b, pages, source_commit="c2")
    assert await business_view(db_session, repo_a) == await business_view(
        db_session, repo_b
    )


# ===========================================================================
# DB-backed: persistence + streak accounting
# ===========================================================================


async def _alpha_row(session: AsyncSession, repo: ScriptedRepo) -> Document:
    return (
        await session.execute(
            select(Document).where(
                Document.repository_id == repo.id,
                Document.doc_type == "wiki",
                Document.slug == "alpha",
            )
        )
    ).scalar_one()


async def test_write_stamps_content_src_and_zero_streak(
    db_session: AsyncSession,
) -> None:
    repo = await _seeded_full(db_session, "edit-persist")
    row = await _alpha_row(db_session, repo)
    assert row.content_src is not None
    assert "[[node:pkg.alpha_main]]" in row.content_src
    # cited_content_hashes snapshots every cited node by UUID.
    assert set(row.cited_content_hashes or {}) == {
        str(repo.node_ids["pkg.alpha_main"]),
        str(repo.node_ids["pkg._alpha_helper"]),
    }
    assert row.edit_streak == 0


async def test_edit_increments_streak(db_session: AsyncSession) -> None:
    repo = await _seeded_full(db_session, "edit-streak-inc")
    await repo.change_node_summary(db_session, "pkg.alpha_main", "alpha summary v2")
    await run_incremental_edits(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        edit_slugs={"alpha"},
        write_slugs={"index"},
    )
    row = await _alpha_row(db_session, repo)
    assert row.edit_streak == 1


async def test_edit_streak_cap_forces_write_and_resets(
    db_session: AsyncSession,
) -> None:
    """With a cap of 2: two consecutive summary-regen edits raise the streak
    to 2; the third dirty cycle finds streak == cap, escalates to a full
    write, and resets the streak to 0."""
    cfg = harness_config(enable_edit_mode=True, edit_streak_cap=2)
    repo = await _seeded_full(db_session, "edit-streak-cap")

    for i, commit in enumerate(("c2", "c3"), start=1):
        await repo.change_node_summary(
            db_session, "pkg.alpha_main", f"alpha summary rev {i}"
        )
        await run_incremental_edits(
            db_session,
            repo,
            STANDARD_PAGES,
            source_commit=commit,
            edit_slugs={"alpha"},
            write_slugs={"index"},
            config=cfg,
        )
        assert (await _alpha_row(db_session, repo)).edit_streak == i

    # Third cycle: streak (2) == cap → alpha is no longer edit-eligible.
    await repo.change_node_summary(db_session, "pkg.alpha_main", "alpha summary rev 3")
    await run_incremental_edits(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c4",
        edit_slugs=set(),
        write_slugs={"alpha", "index"},
        config=cfg,
    )
    assert (await _alpha_row(db_session, repo)).edit_streak == 0
