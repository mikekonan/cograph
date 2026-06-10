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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.incremental import (
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
    harness_config,
    make_retriever,
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
    cfg = harness_config()
    report = await compute_dirty_slugs(
        db_session,
        repository_id=repo.id,
        plan=arts.plan,
        records=records,
        retriever=make_retriever(),
        overview=arts.overview,
        code_top_k=cfg.code_top_k,
        docs_top_k=cfg.docs_top_k,
        graph_pivot_top_k=cfg.graph_pivot_top_k,
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
    once — per-page dirtiness can't shortcut shared evidence. The plan
    carries 8 pages so the 4-page dirty set sits exactly AT the 0.5
    threshold and the run stays incremental."""
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
    assert result.dirty_reasons["alpha"] == "retrieval_drift"


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
            row.retrieval_fingerprint,
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
            after[slug].retrieval_fingerprint,
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
