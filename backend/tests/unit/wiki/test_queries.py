"""Tests for `WikiQueryService` (read-side facade)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.wiki.queries import WikiQueryService
from backend.app.wiki.schemas import (
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
        git_url="https://github.com/test/wiki-llm-queries",
        name="wiki-llm-queries",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node(session: AsyncSession, *, repo_id, qn: str) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path="src/pipeline.py",
        qualified_name=qn,
        node_type=CodeNodeType.FUNCTION,
        name=qn.rsplit(".", 1)[-1],
        language="python",
        start_line=10,
        end_line=42,
        content="def fn(): pass\n",
        content_hash="c" * 64,
    )
    session.add(node)
    await session.flush()
    return node


def _page(
    *,
    slug: str,
    title: str,
    content: str,
    sort_order: int,
    parent_slug: str | None = None,
    citations: list[ResolvedCitation] | None = None,
    source_node_ids=None,
) -> ResolvedPage:
    return ResolvedPage(
        slug=slug,
        title=title,
        parent_slug=parent_slug,
        sort_order=sort_order,
        content=content,
        model="fake-v1",
        citations=citations or [],
        source_node_ids=source_node_ids or [],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
    )


async def test_list_tree_builds_nested_structure(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[
            _page(slug="index", title="Overview", content="# 1", sort_order=0),
            _page(
                slug="architecture",
                title="Arch",
                content="# 2",
                sort_order=1,
                parent_slug="index",
            ),
            _page(
                slug="getting-started",
                title="Setup",
                content="# 3",
                sort_order=2,
                parent_slug="index",
            ),
        ],
    )

    tree = await WikiQueryService().list_tree(session=db_session, repository_id=repo.id)
    assert len(tree) == 1
    root = tree[0]
    assert root.slug == "index"
    child_slugs = sorted(child.slug for child in root.children)
    assert child_slugs == ["architecture", "getting-started"]


async def test_get_page_by_slug_hydrates_citations_and_related_nodes(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="src.pipeline.run")

    citation = ResolvedCitation(
        id=str(node.id),
        kind="node",
        label="run",
        file_path="src/pipeline.py",
        start_line=10,
        end_line=42,
    )
    store = WikiDocumentStore()
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[
            _page(
                slug="architecture",
                title="Architecture",
                content="# Architecture\n\nDetails.",
                sort_order=0,
                citations=[citation],
                source_node_ids=[node.id],
            )
        ],
    )

    page = await WikiQueryService().get_page_by_slug(
        session=db_session, repository_id=repo.id, slug="architecture"
    )
    assert page is not None
    assert page.title == "Architecture"
    assert len(page.citations) == 1
    assert page.citations[0].kind == "node"
    assert page.citations[0].id == str(node.id)
    assert len(page.related_nodes) == 1
    assert page.related_nodes[0].id == node.id
    assert page.related_nodes[0].name == "run"


async def test_get_page_by_slug_returns_none_for_unknown_slug(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    page = await WikiQueryService().get_page_by_slug(
        session=db_session, repository_id=repo.id, slug="missing"
    )
    assert page is None


async def test_get_page_by_slug_hydrates_quality_chips(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    quality = WikiPageQuality(
        code_node_citation_count=2,
        doc_chunk_citation_count=1,
        unresolved_count=0,
        low_confidence_chunk_count=4,
        covers_questions=[ReaderQuestion.HOW_TO_RUN, ReaderQuestion.CONFIGURATION],
        manifest_entries_used=3,
        has_diagram=True,
        agent_turns=5,
        tools_called={"read_node_by_qn": 3, "search_code": 2},
        files_read=4,
        tokens_used=11_500,
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
    await WikiDocumentStore().upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[page],
    )
    loaded = await WikiQueryService().get_page_by_slug(
        session=db_session, repository_id=repo.id, slug="index"
    )
    assert loaded is not None
    assert loaded.quality is not None
    assert loaded.quality.code_node_citation_count == 2
    assert loaded.quality.low_confidence_chunk_count == 4
    assert loaded.quality.has_diagram is True
    assert loaded.quality.covers_questions == ["how-to-run", "configuration"]
    assert loaded.quality.agent_turns == 5
    assert loaded.quality.tools_called == {"read_node_by_qn": 3, "search_code": 2}
    assert loaded.quality.files_read == 4
    assert loaded.quality.tokens_used == 11_500


async def test_get_compact_strips_code_keeps_map_and_orders(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    index_quality = WikiPageQuality(
        covers_questions=[ReaderQuestion.USE_CASES, ReaderQuestion.CONFIGURATION],
    )
    index = ResolvedPage(
        slug="index",
        title="Overview",
        parent_slug=None,
        sort_order=0,
        content=(
            "# Overview\n"
            "The narrative pitch for the service.\n"
            "```go\n"
            "func main() {}\n"
            "```\n"
            "## What it does\n"
            "## How to run\n"
        ),
        model="fake-v1",
        citations=[],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
        quality=index_quality,
    )
    child = _page(
        slug="config",
        title="Config",
        content="# Config\nHow configuration is loaded.\n## Options\n",
        sort_order=1,
        parent_slug="index",
    )
    await WikiDocumentStore().upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[child, index],  # inserted out of order on purpose
    )

    compact = await WikiQueryService().get_compact(
        session=db_session, repository_id=repo.id
    )

    assert [p.slug for p in compact] == ["index", "config"]  # ordered by sort_order
    overview = compact[0]
    assert overview.lead == "The narrative pitch for the service."
    assert "func main" not in overview.lead  # code fence stripped
    assert overview.sections == ["What it does", "How to run"]
    assert overview.covers_questions == ["use-cases", "configuration"]
    assert compact[1].lead == "How configuration is loaded."
    assert compact[1].sections == ["Options"]


async def test_get_compact_gives_index_a_larger_lead_budget(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    long_lead = "alpha " * 100  # ~600 chars of prose
    pages = [
        _page(
            slug="index",
            title="Overview",
            content=f"# Overview\n{long_lead}\n## Section\n",
            sort_order=0,
        ),
        _page(
            slug="deep-dive",
            title="Deep Dive",
            content=f"# Deep Dive\n{long_lead}\n## Section\n",
            sort_order=1,
        ),
    ]
    await WikiDocumentStore().upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=pages,
    )

    compact = await WikiQueryService().get_compact(
        session=db_session, repository_id=repo.id
    )
    by_slug = {p.slug: p for p in compact}
    # index gets the full ~600-char pitch; a regular page is capped at 400.
    assert not by_slug["index"].lead.endswith("…")
    assert by_slug["deep-dive"].lead.endswith("…")
    assert len(by_slug["deep-dive"].lead) < len(by_slug["index"].lead)


async def test_count_pages_counts_only_wiki_variant(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[
            _page(slug="index", title="Overview", content="# 1", sort_order=0),
            _page(slug="api", title="API", content="# 2", sort_order=1),
        ],
    )
    count = await WikiQueryService().count_pages(
        session=db_session, repository_id=repo.id
    )
    assert count == 2
