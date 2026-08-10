"""Tests for `WikiDocumentStore.upsert_pages` and `delete_orphan_pages`."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.models.repository import Repository
from backend.app.wiki.schemas import (
    QualityStatus,
    ReaderQuestion,
    ResolvedCitation,
    ResolvedPage,
    WikiPageQuality,
)
from backend.app.wiki.store import WikiDocumentStore

pytestmark = pytest.mark.asyncio


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-store",
        name="wiki-llm-store",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


def _page(
    *,
    slug: str,
    title: str,
    content: str,
    sort_order: int = 0,
    citations: list[ResolvedCitation] | None = None,
) -> ResolvedPage:
    return ResolvedPage(
        slug=slug,
        title=title,
        parent_slug=None,
        sort_order=sort_order,
        content=content,
        model="fake-v1",
        citations=citations or [],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
    )


async def test_upsert_inserts_new_pages(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    pages = [
        _page(slug="index", title="Overview", content="# Overview", sort_order=0),
        _page(
            slug="architecture",
            title="Architecture",
            content="# Architecture",
            sort_order=1,
        ),
    ]
    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=pages,
    )
    assert len(persisted) == 2
    assert skipped == []
    assert kept_for_quality == []

    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in rows} == {"index", "architecture"}
    assert all(r.doc_type == "wiki" for r in rows)


async def test_upsert_skips_unchanged_content(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    page = _page(slug="index", title="Overview", content="# Same body")
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=[page],
    )

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc124",  # commit changed but content identical
        plan_hash="planhash1",
        model="fake-v1",
        pages=[page],
    )
    assert skipped == ["index"]
    assert kept_for_quality == []
    assert len(persisted) == 1
    # source_commit pointer was bumped despite the skip.
    row = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert row.source_commit == "abc124"


async def test_upsert_updates_changed_content(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# Original")],
    )
    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc124",
        plan_hash="planhash2",
        model="fake-v2",
        pages=[
            _page(
                slug="index",
                title="Overview v2",
                content="# Updated",
                citations=[
                    ResolvedCitation(
                        id=str(uuid4()),
                        kind="node",
                        label="src.run",
                        file_path="src/main.py",
                    )
                ],
            )
        ],
    )
    assert skipped == []
    assert kept_for_quality == []
    assert len(persisted) == 1

    row = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert row.title == "Overview v2"
    assert row.content == "# Updated"
    assert row.model == "fake-v2"
    assert row.source_commit == "abc124"
    assert len(row.citations) == 1


async def test_delete_orphan_pages_removes_missing_slugs(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="hash",
        model="fake-v1",
        pages=[
            _page(slug="index", title="Overview", content="# 1"),
            _page(slug="architecture", title="Arch", content="# 2"),
            _page(slug="deprecated", title="Old", content="# 3"),
        ],
    )

    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo.id,
        keep_slugs=["index", "architecture"],
    )
    assert deleted == 1

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
    assert remaining_slugs == {"index", "architecture"}


async def test_delete_orphan_pages_with_empty_keep_clears_all(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="hash",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# 1")],
    )
    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo.id,
        keep_slugs=[],
    )
    assert deleted == 1


async def test_upsert_does_not_touch_other_repos(db_session: AsyncSession) -> None:
    repo_a = await _make_repo(db_session)
    repo_b = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-store-b",
        name="b",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafe",
    )
    db_session.add(repo_b)
    await db_session.flush()

    store = WikiDocumentStore()
    await store.upsert_pages(
        session=db_session,
        repository_id=repo_a.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# A")],
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo_b.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# B")],
    )

    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo_a.id,
        keep_slugs=[],
    )
    assert deleted == 1

    remaining_b = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo_b.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in remaining_b} == {"index"}


async def test_upsert_persists_quality_payload(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    quality = WikiPageQuality(
        code_node_citation_count=4,
        doc_chunk_citation_count=1,
        unresolved_count=2,
        low_confidence_chunk_count=3,
        covers_questions=[ReaderQuestion.HOW_TO_RUN, ReaderQuestion.PUBLIC_API],
        manifest_entries_used=5,
        has_diagram=True,
    )
    page = ResolvedPage(
        slug="index",
        title="Overview",
        parent_slug=None,
        sort_order=0,
        content="# Overview",
        model="fake-v1",
        citations=[],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
        quality=quality,
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="planhash",
        model="fake-v1",
        pages=[page],
    )
    row = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    assert row.quality is not None
    assert row.quality["code_node_citation_count"] == 4
    assert row.quality["unresolved_count"] == 2
    assert row.quality["low_confidence_chunk_count"] == 3
    assert row.quality["covers_questions"] == ["how-to-run", "public-api"]
    assert row.quality["manifest_entries_used"] == 5
    assert row.quality["has_diagram"] is True

    # Re-running with updated content should refresh quality on the existing row
    quality_v2 = WikiPageQuality(
        code_node_citation_count=10,
        unresolved_count=0,
        has_diagram=False,
    )
    page_v2 = page.model_copy(
        update={"quality": quality_v2, "content": "# Overview v2"}
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="planhash",
        model="fake-v1",
        pages=[page_v2],
    )
    refreshed = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    assert refreshed.quality is not None
    assert refreshed.quality["code_node_citation_count"] == 10
    assert refreshed.quality["unresolved_count"] == 0
    assert refreshed.quality["has_diagram"] is False


async def test_upsert_handles_empty_input(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="m",
        pages=[],
    )
    assert persisted == []
    assert skipped == []
    assert kept_for_quality == []


async def _seed_existing(
    *,
    session: AsyncSession,
    repo: Repository,
    slug: str,
    content: str,
    quality: object,
) -> Document:
    import hashlib

    row = Document(
        repository_id=repo.id,
        sync_run_id=None,
        slug=slug,
        title="Seed",
        doc_type="wiki",
        sort_order=0,
        parent_slug=None,
        source_commit="seed-commit",
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_hash="seed-source-hash",
        model="seed-v1",
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        citations=[],
        quality=quality,
    )
    session.add(row)
    await session.flush()
    return row


def _quality_page(
    *,
    slug: str,
    content: str,
    quality_status: QualityStatus,
    title: str = "Page",
) -> ResolvedPage:
    return ResolvedPage(
        slug=slug,
        title=title,
        parent_slug=None,
        sort_order=0,
        content=content,
        model="run-v2",
        citations=[],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
        quality=WikiPageQuality(quality_status=quality_status),
    )


async def test_reindex_keeps_existing_when_quality_regresses(
    db_session: AsyncSession,
) -> None:
    """Existing OK page + new PARTIAL page → keep existing content + quality;
    bump only sync_run_id and source_commit."""
    repo = await _make_repo(db_session)
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content="# Existing good body",
        quality={"quality_status": "ok", "code_node_citation_count": 5},
    )
    seeded_hash = seeded.content_hash
    seeded_source_hash = seeded.source_hash
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="new-commit",
        plan_hash="new-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content="# New worse body",
                quality_status=QualityStatus.PARTIAL,
            )
        ],
    )

    assert kept_for_quality == ["index"]
    assert skipped == []
    assert persisted == [seeded.id]

    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.content == "# Existing good body"
    assert refreshed.content_hash == seeded_hash
    assert refreshed.source_hash == seeded_source_hash
    assert refreshed.quality is not None
    assert refreshed.quality["quality_status"] == "ok"
    assert refreshed.quality["code_node_citation_count"] == 5
    # source_commit is bumped on the kept row so the audit trail records
    # that the latest run did touch it (decision: keep).
    assert refreshed.source_commit == "new-commit"


async def test_reindex_persists_when_quality_improves(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content="# Old partial body",
        quality={"quality_status": "partial"},
    )
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="new-commit",
        plan_hash="new-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content="# New OK body",
                quality_status=QualityStatus.OK,
            )
        ],
    )

    assert persisted == [seeded.id]
    assert skipped == []
    assert kept_for_quality == []

    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.content == "# New OK body"
    assert refreshed.quality is not None
    assert refreshed.quality["quality_status"] == "ok"


async def test_reindex_persists_first_time_regardless_of_quality(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="run-v1",
        pages=[
            _quality_page(
                slug="index",
                content="# Degraded first run",
                quality_status=QualityStatus.DEGRADED,
            )
        ],
    )

    assert len(persisted) == 1
    assert skipped == []
    assert kept_for_quality == []
    row = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert row.content == "# Degraded first run"
    assert row.quality["quality_status"] == "degraded"


async def test_reindex_persists_when_quality_unchanged(
    db_session: AsyncSession,
) -> None:
    """Same status with different content → take the new content."""
    repo = await _make_repo(db_session)
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content="# Old partial body",
        quality={"quality_status": "partial"},
    )
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="new-commit",
        plan_hash="new-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content="# New partial body",
                quality_status=QualityStatus.PARTIAL,
            )
        ],
    )

    assert persisted == [seeded.id]
    assert skipped == []
    assert kept_for_quality == []
    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.content == "# New partial body"
    assert refreshed.quality["quality_status"] == "partial"


async def test_reindex_persists_when_existing_quality_is_null(
    db_session: AsyncSession,
) -> None:
    """NULL existing quality is treated as unknown — write the new row."""
    repo = await _make_repo(db_session)
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content="# Pre-T1 body",
        quality=None,
    )
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="new-commit",
        plan_hash="new-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content="# Degraded run",
                quality_status=QualityStatus.DEGRADED,
            )
        ],
    )

    assert persisted == [seeded.id]
    assert kept_for_quality == []
    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.content == "# Degraded run"
    assert refreshed.quality["quality_status"] == "degraded"


async def test_reindex_persists_when_existing_quality_is_malformed(
    db_session: AsyncSession,
) -> None:
    """A `quality` payload missing `quality_status` is treated as unknown."""
    repo = await _make_repo(db_session)
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content="# Body with weird quality",
        quality={"unexpected": "shape"},
    )
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="new-commit",
        plan_hash="new-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content="# Degraded run",
                quality_status=QualityStatus.DEGRADED,
            )
        ],
    )

    assert persisted == [seeded.id]
    assert kept_for_quality == []
    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.content == "# Degraded run"
    assert refreshed.quality["quality_status"] == "degraded"


async def test_content_hash_short_circuit_preserves_quality(
    db_session: AsyncSession,
) -> None:
    """Same-content rerun must NOT overwrite recorded quality with a worse
    new payload — the kept body keeps its original recorded quality."""
    repo = await _make_repo(db_session)
    same_content = "# Stable body"
    seeded = await _seed_existing(
        session=db_session,
        repo=repo,
        slug="index",
        content=same_content,
        quality={"quality_status": "ok", "code_node_citation_count": 7},
    )
    store = WikiDocumentStore()

    persisted, skipped, kept_for_quality = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="rerun-commit",
        plan_hash="rerun-hash",
        model="run-v2",
        pages=[
            _quality_page(
                slug="index",
                content=same_content,
                quality_status=QualityStatus.PARTIAL,
            )
        ],
    )

    # Same content → reported as kept-for-quality (worse new telemetry),
    # not as a content-hash skip. Either way, the recorded `quality` JSON
    # must remain `ok`.
    assert persisted == [seeded.id]
    refreshed = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert refreshed.quality["quality_status"] == "ok"
    assert refreshed.quality["code_node_citation_count"] == 7
    # Prove the slug landed in one of the two non-write categories.
    assert "index" in (kept_for_quality + skipped)
