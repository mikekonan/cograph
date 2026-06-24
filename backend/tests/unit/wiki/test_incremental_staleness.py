"""Staleness-safety tests — the inverse of the call-budget suite.

The budget suite proves the incremental path doesn't OVERSPEND; this one
proves it never UNDERSPENDS: a page whose evidence moved must always land
in the rewrite set (serving stale content is the one failure mode worse
than wasted tokens), and pages that legitimately stay clean must remain
byte-identical — not silently re-rendered.

The core property, fuzzed over seeded RNG repos:

    rewrite_set ⊇ { page : cited(page) ∩ changed_nodes ≠ ∅ }

Extra dirty pages (fingerprint drift via shared evidence) are allowed;
missing ones are never allowed.
"""

from __future__ import annotations

import hashlib
import random

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.incremental import (
    DirtyReport,
    compute_dirty_slugs,
    load_artifact,
    load_page_records,
    rehydrate_artifact,
)
from backend.tests.unit.wiki.incremental_harness import (
    STANDARD_PAGES,
    ScriptedPage,
    ScriptedRepo,
    business_view,
    run_full,
    run_incremental,
    seed_standard,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fuzzed superset property
# ---------------------------------------------------------------------------

_NODE_POOL = [f"pkg.node{i}" for i in range(8)]


def _random_repo_shape(
    rng: random.Random,
) -> tuple[dict[str, str], list[ScriptedPage]]:
    """Nodes with overlapping random token vocabularies + pages citing
    random subsets — deliberately NOT namespace-disjoint, so fingerprint
    drift across pages is in play and the superset check is meaningful."""
    vocab = [f"tok{i}" for i in range(12)]
    nodes = {
        qn: f"def f{i}():\n    return '{' '.join(rng.sample(vocab, 4))}'"
        for i, qn in enumerate(_NODE_POOL)
    }
    pages = [ScriptedPage(slug="index", title="Index", purpose="navigation")]
    for i in range(4):
        cites = tuple(rng.sample(_NODE_POOL, rng.randint(1, 3)))
        pages.append(
            ScriptedPage(
                slug=f"page{i}",
                title=f"Page {i}",
                purpose=f"covers {' '.join(rng.sample(vocab, 3))}",
                cites=cites,
            )
        )
    return nodes, pages


@pytest.mark.parametrize("seed", range(20))
async def test_rewrite_set_covers_all_pages_citing_changed_nodes(
    db_session: AsyncSession, seed: int
) -> None:
    rng = random.Random(seed)
    nodes, pages = _random_repo_shape(rng)

    repo = await ScriptedRepo.create(db_session, f"wiki-stale-{seed}")
    for qn, content in nodes.items():
        await repo.add_node(db_session, qn, content=content)
    await run_full(db_session, repo, pages, source_commit="c1")

    changed = set(rng.sample(_NODE_POOL, rng.randint(1, 3)))
    for qn in changed:
        await repo.change_node(db_session, qn, content=nodes[qn] + "  # changed")

    # Probe the dirty predicate directly — the pipeline's threshold/fallback
    # machinery is irrelevant to the safety property.
    artifact = await load_artifact(db_session, repository_id=repo.id)
    assert artifact is not None
    arts = rehydrate_artifact(artifact)
    assert arts is not None
    records = await load_page_records(db_session, repository_id=repo.id)
    report = await compute_dirty_slugs(
        db_session,
        repository_id=repo.id,
        plan=arts.plan,
        records=records,
    )

    must_rewrite = {page.slug for page in pages if set(page.cites) & changed}
    assert must_rewrite <= set(report.dirty), (
        f"seed={seed}: stale pages not flagged dirty: "
        f"{must_rewrite - set(report.dirty)} (dirty={report.dirty})"
    )
    if report.dirty:
        assert "index" in report.dirty


# ---------------------------------------------------------------------------
# Shared evidence fan-out
# ---------------------------------------------------------------------------


async def test_shared_node_change_dirties_every_citing_page(
    db_session: AsyncSession,
) -> None:
    """One node cited by three pages: its change must dirty all three at
    once — per-page dirtiness can't shortcut shared evidence. The four
    single-cite pages are negative controls — citing only their own
    untouched `_main`, they must stay clean, so the exact dirty set proves
    the change reaches every citer and nothing more. Each citer also cites a
    surviving `_main`, so no page collapses and the run stays incremental."""
    pages = [ScriptedPage(slug="index", title="Index", purpose="navigation")]
    for name in ("alpha", "beta", "gamma"):
        pages.append(
            ScriptedPage(
                slug=name,
                title=name.title(),
                purpose=f"covers {name}flow",
                cites=(f"pkg.{name}_main", "pkg.shared_core"),
            )
        )
    for name in ("delta", "epsilon", "zeta", "eta"):
        pages.append(
            ScriptedPage(
                slug=name,
                title=name.title(),
                purpose=f"covers {name}flow",
                cites=(f"pkg.{name}_main",),
            )
        )

    repo = await ScriptedRepo.create(db_session, "wiki-stale-shared")
    for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"):
        await repo.add_node(
            db_session,
            f"pkg.{name}_main",
            content=f"def {name}_main():\n    return '{name}flow v1'",
        )
    await repo.add_node(
        db_session,
        "pkg.shared_core",
        content="def shared_core():\n    return 'sharedcore v1'",
    )
    await run_full(db_session, repo, pages, source_commit="c1")

    await repo.change_node(
        db_session,
        "pkg.shared_core",
        content="def shared_core():\n    return 'sharedcore v2'",
    )
    await run_incremental(
        db_session,
        repo,
        pages,
        source_commit="c2",
        expected_dirty={"alpha", "beta", "gamma", "index"},
    )


# ---------------------------------------------------------------------------
# Summary regeneration with a live node UUID
# ---------------------------------------------------------------------------


async def test_summary_change_with_live_node_uuid_dirties_page(
    db_session: AsyncSession,
) -> None:
    """A neighbor-triggered summary regeneration updates the summary text
    while the node row (and its UUID) survives — the existence check sees
    nothing, so the content hashes inside the fingerprint are the only
    thing standing between the reader and a stale page."""
    repo = await ScriptedRepo.create(db_session, "wiki-stale-summary")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    await repo.change_node_summary(
        db_session, "pkg.alpha_main", "alpha summary v2 — neighbors moved"
    )
    result = await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty={"alpha", "index"},
    )
    assert result.dirty_reasons["alpha"] == "cited_evidence_changed"


# ---------------------------------------------------------------------------
# Clean pages are untouched, not re-rendered
# ---------------------------------------------------------------------------


async def test_clean_pages_kept_byte_identical(
    db_session: AsyncSession,
) -> None:
    """Clean pages after an incremental run: same content bytes, same
    stamps, same quality object — only the audit fields moved. (A silent
    re-render would invalidate reader anchors and bloat diffs even if the
    text looked similar.)"""
    repo = await ScriptedRepo.create(db_session, "wiki-stale-clean")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    async def _rows() -> dict[str, Document]:
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
        return {row.slug: row for row in rows}

    before = {
        slug: (
            row.content,
            row.content_hash,
            row.spec_hash,
            row.cited_fingerprint,
            row.wiki_schema_version,
            dict(row.quality),
            row.id,
        )
        for slug, row in (await _rows()).items()
    }

    await repo.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2'",
    )
    await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c2",
        expected_dirty={"alpha", "index"},
    )

    after = await _rows()
    for slug in ("beta", "gamma"):
        assert before[slug] == (
            after[slug].content,
            after[slug].content_hash,
            after[slug].spec_hash,
            after[slug].cited_fingerprint,
            after[slug].wiki_schema_version,
            dict(after[slug].quality),
            after[slug].id,
        ), f"clean page {slug} was touched beyond audit fields"
        assert after[slug].source_commit == "c2"

    # And the dirty page really did pick up the new evidence: its body
    # embeds the digest of the CURRENT node content (see `page_body`).
    new_digest = hashlib.sha256(
        b"def alpha_main():\n    return 'alphaflow v2'"
    ).hexdigest()[:12]
    view = await business_view(db_session, repo)
    assert new_digest in view["alpha"]["content"]
    assert new_digest not in view["beta"]["content"]


# ---------------------------------------------------------------------------
# Deploy safety: a NULL cited_fingerprint adopts, it never dirties ($0 deploy)
# ---------------------------------------------------------------------------


async def test_null_cited_fingerprint_adopts_without_rewrite(
    db_session: AsyncSession,
) -> None:
    """The $0-deploy guarantee, end to end.

    Reproduce the deploy that introduces the column: run a full wiki, then
    NULL every page's `cited_fingerprint` exactly as the fresh migration
    leaves it. The next sync — same commit, no node moved — must rewrite
    NOTHING (`run_incremental` installs a StrictProvider that raises on any
    LLM call) yet stamp every row with the freshly recomputed 64-char
    fingerprint it adopted from the already-current bodies. Under the old
    whole-bundle stamp a missing fingerprint was dirty, so this same deploy
    would have regenerated the entire wiki — the exact token blow-up the
    cited stamp is here to kill.
    """
    repo = await ScriptedRepo.create(db_session, "wiki-adopt")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    await db_session.execute(
        update(Document)
        .where(Document.repository_id == repo.id, Document.doc_type == "wiki")
        .values(cited_fingerprint=None)
    )
    await db_session.flush()

    # Same commit, nothing changed → zero dirty. StrictProvider proves no
    # page was rewritten; the run only adopts the NULL stamps.
    await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c1",
        expected_dirty=set(),
    )

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
    assert rows
    for row in rows:
        assert (
            row.cited_fingerprint is not None and len(row.cited_fingerprint) == 64
        ), f"{row.slug} was not adopted (cited_fingerprint={row.cited_fingerprint!r})"


# ---------------------------------------------------------------------------
# Coverage collapse: the residual re-plan signal
#
# A page is *collapsed* when its ENTIRE cited subject is gone — every cited
# node id and every cited chunk id absent from the live graph. That, not raw
# dirty volume, is what now forces a full re-plan (`full_rebuild_collapse_ratio`).
# These probe `compute_dirty_slugs` directly; the pipeline's fallback wiring is
# exercised from the other side in test_incremental_equivalence::
# test_many_edited_pages_stay_incremental (high dirty, zero collapse → stays
# incremental). Both reference `DirtyReport.collapsed` /
# `coverage_collapse_ratio`, which the pre-collapse report lacks — so both
# fail with AttributeError on the old code and pass on the new.
# ---------------------------------------------------------------------------


async def _collapse_report(session: AsyncSession, repo: ScriptedRepo) -> DirtyReport:
    artifact = await load_artifact(session, repository_id=repo.id)
    assert artifact is not None
    arts = rehydrate_artifact(artifact)
    assert arts is not None
    records = await load_page_records(session, repository_id=repo.id)
    return await compute_dirty_slugs(
        session, repository_id=repo.id, plan=arts.plan, records=records
    )


async def test_coverage_collapse_counts_only_fully_orphaned_pages(
    db_session: AsyncSession,
) -> None:
    """Collapse means a page lost its WHOLE subject — nothing less counts.
    Three pages, three fates in one run:

      * beta — `change_node_body`: same UUID, new content. Dirty, but the
        subject is alive → NOT collapsed.
      * alpha — `change_node` on one of its two cites: the old UUID is gone
        but `_alpha_helper` survives → partial loss → NOT collapsed.
      * gamma — `delete_node` on its sole cite: subject fully gone → COLLAPSED.

    So `collapsed == {"gamma"}` even though all four pages are dirty — the
    exact distinction the $64 storm lacked, where every dirty page was a
    reason to rebuild."""
    repo = await ScriptedRepo.create(db_session, "wiki-collapse-precision")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    await repo.change_node_body(
        db_session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v2'",
    )
    await repo.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2'",
    )
    await repo.delete_node(db_session, "pkg.gamma_main")

    report = await _collapse_report(db_session, repo)

    assert report.collapsed == frozenset({"gamma"})
    assert "alpha" not in report.collapsed  # partial survival
    assert "beta" not in report.collapsed  # in-place edit, UUID alive
    assert report.coverage_collapse_ratio == 1 / 4
    # Collapse is a strict subset of dirty, not a synonym for it.
    assert {"alpha", "beta", "gamma", "index"} <= set(report.dirty)


async def test_coverage_collapse_ratio_crosses_threshold_on_mass_deletion(
    db_session: AsyncSession,
) -> None:
    """Delete every cited node: alpha/beta/gamma each lose their whole
    subject → collapsed; `index` cites nothing, so it has no subject to lose
    and is never collapsed. 3 of 4 pages = 0.75 > the 0.5 default the
    pipeline gates a re-plan on — proof the ratio the fallback reads actually
    crosses the line on real subject loss, and that an uncited page can't
    inflate it."""
    repo = await ScriptedRepo.create(db_session, "wiki-collapse-mass")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    for qn in (
        "pkg.alpha_main",
        "pkg._alpha_helper",
        "pkg.beta_main",
        "pkg.gamma_main",
    ):
        await repo.delete_node(db_session, qn)

    report = await _collapse_report(db_session, repo)

    assert report.collapsed == frozenset({"alpha", "beta", "gamma"})
    assert "index" not in report.collapsed  # no cites → never a collapse
    assert report.coverage_collapse_ratio == 3 / 4
    assert report.coverage_collapse_ratio > 0.5  # would trip the re-plan gate
