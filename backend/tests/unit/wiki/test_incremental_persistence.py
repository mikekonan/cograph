"""Persistence + lifecycle tests for the incremental wiki path.

Write-side stamping (PR1):

- `run_wiki_generation` persists/refreshes the singleton `wiki_artifacts`
  row (structural_hash, plan, overview, mindmap, model ids).
- Every upserted page row carries `spec_hash`, `retrieval_fingerprint`,
  and `wiki_schema_version` — on both the full-write and the
  content-hash-skip paths.
- The quality-keep path does NOT stamp: a kept row still holds old
  content, so its stale fingerprint must keep marking it dirty.

Row lifecycle under incremental control flow (PR2):

- The orphan sweep keeps clean (not rewritten) pages and rows owed to
  transiently failed pages; re-planned-away slugs still get deleted.
- A transient page failure leaves the old row serving and the page dirty,
  so the next sync retries it.
- `force_full` rewrites everything even with perfectly reusable artifacts.
- Decision 4 (content skip) upgrades recorded quality when the rewrite
  healed the page, and backfills it when it was unknown.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.document import Document
from backend.app.models.repository import Repository
from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.wiki.incremental import rehydrate_artifact
from backend.app.wiki.llm_client import (
    FakeStructuredProvider,
    StructuredCompletionError,
)
from backend.app.wiki.pipeline import WikiGenerationConfig, run_wiki_generation
from backend.app.wiki.retrieval import WikiRetrievalService
from backend.app.wiki.schemas import (
    MindMap,
    PagePlan,
    QualityStatus,
    RepoOverview,
    ResolvedPage,
    WikiPageQuality,
)
from backend.app.wiki.store import WikiDocumentStore
from backend.app.wiki.version import WIKI_SCHEMA_VERSION
from backend.tests.unit.wiki.incremental_harness import (
    STANDARD_PAGES,
    ScriptedPage,
    ScriptedRepo,
    assert_drained,
    business_view,
    queue_full_run,
    queue_page_turns,
    run_full,
    run_incremental,
    run_pipeline,
    seed_standard,
)

pytestmark = pytest.mark.asyncio


class _NoopHybrid:
    async def retrieve(self, session: AsyncSession, **_kwargs: object) -> list:
        return []


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-incremental-persist",
        name="wiki-incremental-persist",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


def _queue_fake_pipeline(
    provider: FakeStructuredProvider, *, slugs: list[str], body_suffix: str = ""
) -> None:
    provider.queue(
        RepoOverview(
            one_line="Incremental persist test repo",
            long_description="Fixture repo for wiki_artifacts persistence tests.",
        ).model_dump_json()
    )
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
    for slug in slugs:
        provider.queue_tool_turn(
            tool_uses=[
                (
                    "write_page",
                    {
                        "markdown": f"# {slug.title()}\n\nBody for `{slug}`.{body_suffix}"
                    },
                )
            ]
        )
        provider.queue_tool_turn(text="")


def _retriever() -> WikiRetrievalService:
    return WikiRetrievalService(
        hybrid=_NoopHybrid(),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
    )


async def _run(
    session: AsyncSession,
    repo: Repository,
    *,
    slugs: list[str],
    source_commit: str,
    body_suffix: str = "",
) -> None:
    fake = FakeStructuredProvider()
    _queue_fake_pipeline(fake, slugs=slugs, body_suffix=body_suffix)
    result = await run_wiki_generation(
        session=session,
        repository_id=repo.id,
        source_commit=source_commit,
        sync_run_id=None,
        llm=fake,
        retriever=_retriever(),
        config=WikiGenerationConfig(write_concurrency=1),
    )
    assert result.errors == []


# ---------------------------------------------------------------------------
# wiki_artifacts persistence via run_wiki_generation
# ---------------------------------------------------------------------------


async def test_run_persists_artifact_row(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    slugs = ["index", "architecture", "getting-started"]
    await _run(db_session, repo, slugs=slugs, source_commit="commit-1")

    artifact = (
        await db_session.execute(
            select(WikiArtifact).where(WikiArtifact.repository_id == repo.id)
        )
    ).scalar_one()
    assert artifact.source_commit == "commit-1"
    assert artifact.wiki_schema_version == WIKI_SCHEMA_VERSION
    assert len(artifact.structural_hash) == 64
    assert len(artifact.plan_hash) == 64
    assert artifact.chat_model == FakeStructuredProvider().model
    assert artifact.embed_model == "fake-embed-v1"

    rehydrated = rehydrate_artifact(artifact)
    assert rehydrated is not None
    assert [p.slug for p in rehydrated.plan.pages] == slugs
    assert rehydrated.overview.one_line == "Incremental persist test repo"


async def test_rerun_updates_artifact_in_place(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    slugs = ["index", "architecture", "getting-started"]
    await _run(db_session, repo, slugs=slugs, source_commit="commit-1")
    await _run(db_session, repo, slugs=slugs, source_commit="commit-2")

    artifacts = (
        (
            await db_session.execute(
                select(WikiArtifact).where(WikiArtifact.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    # UNIQUE(repository_id): the rerun upserts, never duplicates.
    assert len(artifacts) == 1
    assert artifacts[0].source_commit == "commit-2"
    # Same repo content → same structural hash across runs.
    assert len(artifacts[0].structural_hash) == 64


async def test_run_stamps_pages_with_incremental_keys(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    slugs = ["index", "architecture", "getting-started"]
    await _run(db_session, repo, slugs=slugs, source_commit="commit-1")

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
    assert {r.slug for r in rows} == set(slugs)
    for row in rows:
        assert row.wiki_schema_version == WIKI_SCHEMA_VERSION
        assert row.spec_hash is not None and len(row.spec_hash) == 64
        assert (
            row.retrieval_fingerprint is not None
            and len(row.retrieval_fingerprint) == 64
        )


async def test_content_skip_rerun_restamps_pages(db_session: AsyncSession) -> None:
    """Decision 4 (identical content → body write skipped) must still
    refresh the stamps — the page is current, it just didn't change."""
    repo = await _make_repo(db_session)
    slugs = ["index", "architecture", "getting-started"]
    await _run(db_session, repo, slugs=slugs, source_commit="commit-1")

    # Wipe the stamps to prove the second (skip-path) run rewrites them.
    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.spec_hash = None
        row.retrieval_fingerprint = None
        row.wiki_schema_version = None
    await db_session.flush()

    await _run(db_session, repo, slugs=slugs, source_commit="commit-1")
    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        assert row.wiki_schema_version == WIKI_SCHEMA_VERSION
        assert row.spec_hash is not None
        assert row.retrieval_fingerprint is not None


# ---------------------------------------------------------------------------
# Store-level stamping rules
# ---------------------------------------------------------------------------


def _resolved_page(
    *, slug: str, content: str, quality_status: QualityStatus
) -> ResolvedPage:
    return ResolvedPage(
        slug=slug,
        title=slug.title(),
        parent_slug=None,
        sort_order=0,
        content=content,
        model="fake-v1",
        citations=[],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
        quality=WikiPageQuality(quality_status=quality_status),
    )


async def test_quality_keep_does_not_stamp(db_session: AsyncSession) -> None:
    """Decision 3 (new quality regresses → old content kept) must leave the
    old stamps in place: the kept row holds *old* content and citations, so
    a fresh fingerprint would wrongly mark it clean and freeze a page the
    pipeline still owes a better rewrite for."""
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    ok_page = _resolved_page(
        slug="index", content="# Good body", quality_status=QualityStatus.OK
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="commit-1",
        plan_hash="plan-1",
        model="fake-v1",
        pages=[ok_page],
        wiki_schema_version=WIKI_SCHEMA_VERSION,
        spec_hashes_by_slug={"index": "spec-old"},
        fingerprints_by_slug={"index": "fp-old"},
    )

    degraded_page = _resolved_page(
        slug="index", content="# Worse body", quality_status=QualityStatus.DEGRADED
    )
    _, _, kept = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="commit-2",
        plan_hash="plan-2",
        model="fake-v1",
        pages=[degraded_page],
        wiki_schema_version=WIKI_SCHEMA_VERSION,
        spec_hashes_by_slug={"index": "spec-new"},
        fingerprints_by_slug={"index": "fp-new"},
    )
    assert kept == ["index"]

    row = (
        await db_session.execute(
            select(Document).where(
                Document.repository_id == repo.id, Document.slug == "index"
            )
        )
    ).scalar_one()
    assert row.content == "# Good body"
    # Stamps untouched — still describing the content the row actually holds.
    assert row.spec_hash == "spec-old"
    assert row.retrieval_fingerprint == "fp-old"
    # Audit fields still bump.
    assert row.source_commit == "commit-2"


async def test_full_update_overwrites_stamps(db_session: AsyncSession) -> None:
    """Decision 5 (changed content, quality not regressing) rewrites the
    stamps along with the body."""
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    for commit, content, spec, fp in (
        ("commit-1", "# v1", "spec-1", "fp-1"),
        ("commit-2", "# v2", "spec-2", "fp-2"),
    ):
        await store.upsert_pages(
            session=db_session,
            repository_id=repo.id,
            sync_run_id=None,
            source_commit=commit,
            plan_hash="plan",
            model="fake-v1",
            pages=[
                _resolved_page(
                    slug="index", content=content, quality_status=QualityStatus.OK
                )
            ],
            wiki_schema_version=WIKI_SCHEMA_VERSION,
            spec_hashes_by_slug={"index": spec},
            fingerprints_by_slug={"index": fp},
        )

    row = (
        await db_session.execute(
            select(Document).where(
                Document.repository_id == repo.id, Document.slug == "index"
            )
        )
    ).scalar_one()
    assert row.content == "# v2"
    assert row.spec_hash == "spec-2"
    assert row.retrieval_fingerprint == "fp-2"
    assert row.wiki_schema_version == WIKI_SCHEMA_VERSION


async def test_content_skip_upgrades_quality_when_strictly_better(
    db_session: AsyncSession,
) -> None:
    """Decision 4 with a healed page: a degraded row rewritten to identical
    bytes at quality=ok must record the recovery — otherwise the
    `quality_degraded` dirty clause re-flags (and re-pays for) the page on
    every subsequent sync, forever."""
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    for commit, status in (
        ("commit-1", QualityStatus.DEGRADED),
        ("commit-2", QualityStatus.OK),
    ):
        await store.upsert_pages(
            session=db_session,
            repository_id=repo.id,
            sync_run_id=None,
            source_commit=commit,
            plan_hash="plan",
            model="fake-v1",
            pages=[
                _resolved_page(
                    slug="index", content="# Same body", quality_status=status
                )
            ],
            wiki_schema_version=WIKI_SCHEMA_VERSION,
            spec_hashes_by_slug={"index": "spec"},
            fingerprints_by_slug={"index": "fp"},
        )

    row = (
        await db_session.execute(
            select(Document).where(
                Document.repository_id == repo.id, Document.slug == "index"
            )
        )
    ).scalar_one()
    assert row.quality["quality_status"] == "ok"


async def test_content_skip_backfills_unknown_quality(
    db_session: AsyncSession,
) -> None:
    """Decision 4 with `quality=NULL` (pre-quality-era row): the rerun's
    quality is recorded so the `quality_unknown` dirty clause stops firing."""
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="commit-1",
        plan_hash="plan",
        model="fake-v1",
        pages=[
            _resolved_page(
                slug="index", content="# Same body", quality_status=QualityStatus.OK
            )
        ],
        wiki_schema_version=WIKI_SCHEMA_VERSION,
    )
    row = (
        await db_session.execute(
            select(Document).where(
                Document.repository_id == repo.id, Document.slug == "index"
            )
        )
    ).scalar_one()
    row.quality = None
    await db_session.flush()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="commit-2",
        plan_hash="plan",
        model="fake-v1",
        pages=[
            _resolved_page(
                slug="index", content="# Same body", quality_status=QualityStatus.OK
            )
        ],
        wiki_schema_version=WIKI_SCHEMA_VERSION,
    )
    await db_session.flush()
    assert row.quality is not None
    assert row.quality["quality_status"] == "ok"


# ---------------------------------------------------------------------------
# Row lifecycle under incremental control flow
# ---------------------------------------------------------------------------


async def _wiki_slugs(session: AsyncSession, repo: ScriptedRepo) -> set[str]:
    rows = (
        (
            await session.execute(
                select(Document).where(
                    Document.repository_id == repo.id,
                    Document.doc_type == "wiki",
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.slug for row in rows}


async def test_orphan_sweep_preserves_clean_pages(
    db_session: AsyncSession,
) -> None:
    """THE incremental regression risk: clean pages are not in the resolved
    set, so a keep-list built only from resolved∪failed would delete the
    entire untouched wiki on every partial run."""
    repo = await ScriptedRepo.create(db_session, "wiki-life-orphan")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")
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
    assert await _wiki_slugs(db_session, repo) == {
        "index",
        "alpha",
        "beta",
        "gamma",
    }


async def test_replanned_away_pages_still_deleted(
    db_session: AsyncSession,
) -> None:
    """Orphan deletion must keep working through the salvage path: a
    structural change re-plans the wiki, the new plan drops gamma and adds
    delta — gamma's row goes away while clean alpha/beta survive unwritten."""
    repo = await ScriptedRepo.create(db_session, "wiki-life-replan")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    await repo.add_node(
        db_session,
        "pkg.delta_main",
        content="def delta_main():\n    return 'deltaflow v1'",
    )
    pages_v2 = [page for page in STANDARD_PAGES if page.slug != "gamma"] + [
        ScriptedPage(
            slug="delta",
            title="Delta",
            purpose="Documents the deltaflow subsystem",
            cites=("pkg.delta_main",),
        )
    ]
    provider = FakeStructuredProvider()
    queue_full_run(provider, repo, pages_v2, write_slugs={"delta", "index"})
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")
    assert result.errors == []
    assert result.mode == "full"
    assert_drained(provider)
    assert await _wiki_slugs(db_session, repo) == {
        "index",
        "alpha",
        "beta",
        "delta",
    }


class _FlakyProvider(FakeStructuredProvider):
    """Raises a transient completion error on the Nth tool-loop call."""

    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call
        self._call_count = 0

    async def complete_with_tools(self, **kwargs: object) -> object:
        self._call_count += 1
        if self._call_count == self._fail_on_call:
            raise StructuredCompletionError("transient provider blip")
        return await super().complete_with_tools(**kwargs)


async def test_transient_failure_keeps_old_row_then_retries_next_sync(
    db_session: AsyncSession,
) -> None:
    """A dirty page whose agent dies transiently: the run reports the
    failure, the OLD row keeps serving readers (kept by the orphan sweep,
    stamps untouched), and the very next sync retries exactly that page."""
    repo = await ScriptedRepo.create(db_session, "wiki-life-flaky")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")
    old_digest = hashlib.sha256(
        b"def alpha_main():\n    return 'alphaflow v1'"
    ).hexdigest()[:12]
    await repo.change_node(
        db_session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v2'",
    )

    # Write order is plan order (index, alpha); index costs calls 1-2, so
    # alpha's first loop is call 3.
    provider = _FlakyProvider(fail_on_call=3)
    index = next(p for p in STANDARD_PAGES if p.slug == "index")
    queue_page_turns(provider, repo, index)
    result = await run_pipeline(db_session, repo, llm=provider, source_commit="c2")
    assert result.errors == ["page_failed:alpha"]
    assert result.mode == "incremental"
    assert set(result.dirty_reasons) == {"alpha", "index"}
    assert_drained(provider)

    view = await business_view(db_session, repo)
    assert set(view) == {"index", "alpha", "beta", "gamma"}
    assert old_digest in view["alpha"]["content"]  # old row still serving

    # Next sync: alpha is still dirty (stale stamps), nothing else is.
    await run_incremental(
        db_session,
        repo,
        STANDARD_PAGES,
        source_commit="c3",
        expected_dirty={"alpha", "index"},
    )
    new_digest = hashlib.sha256(
        b"def alpha_main():\n    return 'alphaflow v2'"
    ).hexdigest()[:12]
    view = await business_view(db_session, repo)
    assert new_digest in view["alpha"]["content"]


async def test_force_full_rewrites_everything_despite_valid_artifacts(
    db_session: AsyncSession,
) -> None:
    """The OWNER rebuild button: artifacts and stamps are perfectly
    reusable, yet `force_full=True` re-plans and rewrites every page."""
    repo = await ScriptedRepo.create(db_session, "wiki-life-force")
    await seed_standard(db_session, repo)
    await run_full(db_session, repo, STANDARD_PAGES, source_commit="c1")

    provider = FakeStructuredProvider()
    queue_full_run(provider, repo, STANDARD_PAGES)
    result = await run_pipeline(
        db_session,
        repo,
        llm=provider,
        source_commit="c2",
        force_full=True,
    )
    assert result.errors == []
    assert result.mode == "full"
    assert result.pages_written == len(STANDARD_PAGES)
    assert result.pages_clean_skipped == 0
    assert result.dirty_reasons == {}
    assert_drained(provider)
