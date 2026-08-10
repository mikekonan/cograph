"""Tests for pipeline stages 2-3 (analyze_repo + plan_pages) and orchestration."""

from __future__ import annotations

from uuid import UUID

import pytest

from backend.app.wiki.context import RepoContext
from backend.app.wiki.llm_client import (
    FakeStructuredProvider,
    StructuredCompletionError,
)
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    WikiPlanError,
    _normalize_plan,
    analyze_repo,
    plan_pages,
)
from backend.app.wiki.schemas import (
    PagePlan,
    PageSpec,
    RepoOverview,
)


def _ctx() -> RepoContext:
    return RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000099"),
        commit_sha="abc123",
        file_tree_hash="0" * 64,
        docs_hash="0" * 64,
        summaries_hash="0" * 64,
        identity_hash="0" * 64,
        previous_run_slugs=["index", "architecture"],
    )


@pytest.mark.asyncio
async def test_analyze_repo_parses_clean_json() -> None:
    fake = FakeStructuredProvider()
    fake.queue(
        RepoOverview(
            one_line="Test repo",
            long_description="A small fixture used in unit tests for stage 2.",
            primary_languages=["python"],
        ).model_dump_json()
    )
    overview = await analyze_repo(llm=fake, context=_ctx())
    assert overview.one_line == "Test repo"
    assert overview.primary_languages == ["python"]


@pytest.mark.asyncio
async def test_analyze_repo_retries_once_on_bad_json() -> None:
    fake = FakeStructuredProvider()
    fake.queue("not json at all")
    fake.queue(
        RepoOverview(
            one_line="ok", long_description="recovered on retry"
        ).model_dump_json()
    )
    overview = await analyze_repo(llm=fake, context=_ctx())
    assert overview.one_line == "ok"
    # both responses consumed
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_analyze_repo_raises_after_two_failures() -> None:
    fake = FakeStructuredProvider()
    fake.queue("garbage 1")
    fake.queue("garbage 2")
    with pytest.raises(StructuredCompletionError):
        await analyze_repo(llm=fake, context=_ctx())


@pytest.mark.asyncio
async def test_plan_pages_normalizes_and_returns_plan() -> None:
    fake = FakeStructuredProvider()
    raw_plan = {
        "pages": [
            {"slug": "Architecture", "title": "Architecture", "purpose": "Design"},
            {"slug": "index", "title": "Overview", "purpose": "Landing"},
            {"slug": "deep dive!", "title": "Deep dive", "purpose": "Internals"},
            {"slug": "deep-dive", "title": "Other deep", "purpose": "Variant"},
            {"slug": "ops", "title": "Operations", "purpose": "Run it"},
        ]
    }
    import json

    fake.queue(json.dumps(raw_plan))
    overview = RepoOverview(one_line="x", long_description="...")
    plan = await plan_pages(
        llm=fake,
        context=_ctx(),
        overview=overview,
        config=WikiGenerationConfig(),
    )
    slugs = [p.slug for p in plan.pages]
    assert slugs[0] == "index"
    # 'Architecture' was promoted to 'architecture'; 'deep dive!' got cleaned;
    # the duplicate 'deep-dive' got a -2 suffix.
    assert "architecture" in slugs
    assert "deep-dive" in slugs
    assert "deep-dive-2" in slugs
    assert "ops" in slugs
    # No raw whitespace or punctuation left.
    for slug in slugs:
        assert " " not in slug
        assert "!" not in slug


@pytest.mark.asyncio
async def test_plan_pages_raises_when_too_few_pages() -> None:
    fake = FakeStructuredProvider()
    fake.queue(
        PagePlan(
            pages=[
                PageSpec(slug="index", title="x", purpose="y"),
                PageSpec(slug="lonely", title="x", purpose="y"),
            ]
        ).model_dump_json()
    )
    overview = RepoOverview(one_line="hello", long_description="world")
    with pytest.raises(WikiPlanError):
        await plan_pages(
            llm=fake,
            context=_ctx(),
            overview=overview,
            config=WikiGenerationConfig(page_count_min=3),
        )


@pytest.mark.asyncio
async def test_plan_pages_raises_when_llm_returns_garbage() -> None:
    fake = FakeStructuredProvider()
    fake.queue("not json")
    fake.queue("still not json")
    overview = RepoOverview(one_line="repo", long_description="...")
    with pytest.raises(WikiPlanError):
        await plan_pages(
            llm=fake,
            context=_ctx(),
            overview=overview,
            config=WikiGenerationConfig(page_count_min=3),
        )


def test_normalize_plan_caps_at_max() -> None:
    pages = [PageSpec(slug=f"p{i}", title=f"P {i}", purpose=str(i)) for i in range(20)]
    plan = PagePlan(pages=pages)
    normalized = _normalize_plan(plan, WikiGenerationConfig(page_count_max=5))
    assert len(normalized.pages) == 5
    assert normalized.pages[0].slug == "index"  # first page promoted


def test_normalize_plan_promotes_existing_index_to_front() -> None:
    plan = PagePlan(
        pages=[
            PageSpec(slug="alpha", title="A", purpose="."),
            PageSpec(slug="beta", title="B", purpose="."),
            PageSpec(slug="index", title="Home", purpose="."),
        ]
    )
    normalized = _normalize_plan(plan, WikiGenerationConfig())
    assert [p.slug for p in normalized.pages] == ["index", "alpha", "beta"]
    # The original 'alpha' was NOT renamed to 'index'.
    assert normalized.pages[0].title == "Home"


def test_normalize_plan_re_roots_orphan_parent_slug_to_index() -> None:
    plan = PagePlan(
        pages=[
            PageSpec(slug="index", title="Home", purpose="."),
            PageSpec(
                slug="orphan",
                title="Orphan",
                parent_slug="does-not-exist",
                purpose=".",
            ),
        ]
    )
    normalized = _normalize_plan(plan, WikiGenerationConfig())
    orphan = next(p for p in normalized.pages if p.slug == "orphan")
    # Orphans are now re-rooted to `index` (flat 2-level wiki contract),
    # not left as additional top-level siblings of `index`.
    assert orphan.parent_slug == "index"


def test_normalize_plan_flattens_to_two_levels() -> None:
    plan = PagePlan(
        pages=[
            PageSpec(slug="index", title="Home", purpose="."),
            PageSpec(slug="api", title="API", purpose="."),
            PageSpec(
                slug="api-handlers",
                title="Handlers",
                parent_slug="api",
                purpose=".",
            ),
            PageSpec(
                slug="api-handlers-detail",
                title="Detail",
                parent_slug="api-handlers",
                purpose=".",
            ),
        ]
    )
    normalized = _normalize_plan(plan, WikiGenerationConfig())
    # Strict flatten: every non-index page is a direct child of `index`.
    for page in normalized.pages:
        if page.slug == "index":
            assert page.parent_slug is None
        else:
            assert page.parent_slug == "index"


def test_normalize_plan_self_parent_collapses_to_index() -> None:
    plan = PagePlan(
        pages=[
            PageSpec(slug="index", title="Home", purpose="."),
            PageSpec(
                slug="loop",
                title="Loop",
                parent_slug="loop",  # self-reference
                purpose=".",
            ),
            PageSpec(slug="ops", title="Ops", purpose="."),
        ]
    )
    normalized = _normalize_plan(plan, WikiGenerationConfig())
    loop = next(p for p in normalized.pages if p.slug == "loop")
    # Self-parent gets dropped to None by the depth pass, then the
    # flatten pass re-roots it to `index` (no top-level siblings allowed).
    assert loop.parent_slug == "index"


def test_normalize_plan_keeps_index_at_top_level() -> None:
    plan = PagePlan(
        pages=[
            PageSpec(slug="api", title="API", purpose="."),
            PageSpec(
                slug="index",
                title="Home",
                parent_slug="api",  # planner mistakenly nested the index
                purpose=".",
            ),
            PageSpec(slug="ops", title="Ops", purpose="."),
        ]
    )
    normalized = _normalize_plan(plan, WikiGenerationConfig())
    assert normalized.pages[0].slug == "index"
    assert normalized.pages[0].parent_slug is None


def test_normalize_plan_drops_orphan_parents_when_capping() -> None:
    pages = [PageSpec(slug="index", title="Home", purpose=".")]
    pages.extend(
        PageSpec(slug=f"p{i}", title=f"P {i}", purpose=".") for i in range(1, 5)
    )
    pages.append(
        PageSpec(slug="child", title="Child", parent_slug="p4", purpose=".")
    )
    plan = PagePlan(pages=pages)
    # Cap below where p4 sits — child's parent gets dropped, so child must
    # be re-rooted to `index` (flat-tree contract), not left dangling.
    normalized = _normalize_plan(plan, WikiGenerationConfig(page_count_max=4))
    assert len(normalized.pages) == 4
    if any(p.slug == "child" for p in normalized.pages):
        child = next(p for p in normalized.pages if p.slug == "child")
        assert child.parent_slug == "index"


@pytest.mark.asyncio
async def test_fake_structured_provider_records_blocks() -> None:
    fake = FakeStructuredProvider()
    fake.queue(RepoOverview(one_line="x", long_description="y").model_dump_json())
    await analyze_repo(llm=fake, context=_ctx())
    assert len(fake.calls) == 1
    blocks = fake.calls[0]["blocks"]
    # First block (repo-context) must be cacheable; user block is fresh.
    assert blocks[0][1] is True
    assert blocks[1][1] is False
