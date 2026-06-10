"""PR1 persistence tests: wiki_artifacts row + per-page incremental stamps.

Covers the write side only — PR1 changes no control flow, it just records
everything the future incremental path will key on:

- `run_wiki_generation` persists/refreshes the singleton `wiki_artifacts`
  row (structural_hash, plan, overview, mindmap, model ids).
- Every upserted page row carries `spec_hash`, `retrieval_fingerprint`,
  and `wiki_schema_version` — on both the full-write and the
  content-hash-skip paths.
- The quality-keep path does NOT stamp: a kept row still holds old
  content, so its stale fingerprint must keep marking it dirty.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.document import Document
from backend.app.models.repository import Repository
from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.wiki.incremental import rehydrate_artifact
from backend.app.wiki.llm_client import FakeStructuredProvider
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
