"""Tests for Stage 4 — agentic `write_pages` loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.app.wiki.context import RepoContext
from backend.app.wiki.llm_client import (
    FakeStructuredProvider,
    StructuredCompletionError,
    ToolUseResult,
)
from backend.app.wiki.pipeline import WikiGenerationConfig, write_pages
from backend.app.wiki.retrieval import PageBundle
from backend.app.wiki.schemas import PagePlan, PageSpec, RepoOverview

pytestmark = pytest.mark.asyncio


def _ctx() -> RepoContext:
    return RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        commit_sha="cafef00d",
        readme_text="# fixture",
        file_tree_hash="a" * 64,
        docs_hash="b" * 64,
        summaries_hash="c" * 64,
        identity_hash="d" * 64,
        previous_run_slugs=[],
    )


def _plan(*slugs: str) -> PagePlan:
    return PagePlan(
        pages=[
            PageSpec(slug=slug, title=slug.title(), purpose=f"about {slug}")
            for slug in slugs
        ]
    )


def _stub_retriever(bundles: dict[str, PageBundle] | None = None):
    """Retriever stub: `for_page` returns canned `PageBundle`s; the agent
    surface (`hybrid`, `embedder`) attributes are present but unused in
    these unit tests because the FakeStructuredProvider never actually
    routes a tool call through them."""
    bundles = bundles or {}

    async def for_page(*, purpose: str, **_kwargs):
        for slug, bundle in bundles.items():
            if f"about {slug}" == purpose:
                return bundle
        return PageBundle()

    retriever = AsyncMock()
    retriever.for_page.side_effect = for_page
    retriever.hybrid = object()
    retriever.embedder = None
    return retriever


# ---------------------------------------------------------------------------
# Happy-path agent loops
# ---------------------------------------------------------------------------


async def test_write_pages_captures_markdown_via_write_page_tool() -> None:
    """The agent's terminal `write_page` call sets the page body."""
    plan = _plan("index", "architecture")
    fake = FakeStructuredProvider()
    # Two pages → two independent loops, each ending in write_page.
    fake.queue_tool_turn(
        text="ok shipping",
        tool_uses=[("write_page", {"markdown": "# Overview\n\nLanding."})],
    )
    fake.queue_tool_turn(text="")  # post-write_page end_turn for page 1
    fake.queue_tool_turn(
        text="ok shipping",
        tool_uses=[("write_page", {"markdown": "# Architecture\n\nDesign."})],
    )
    fake.queue_tool_turn(text="")  # end_turn for page 2

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(write_concurrency=1),
    )

    assert failures == []
    assert [d.slug for d in drafts] == ["index", "architecture"]
    assert drafts[0].body_md == "# Overview\n\nLanding."
    assert drafts[1].body_md == "# Architecture\n\nDesign."
    # Telemetry from the loop is preserved on the draft.
    assert drafts[0].agent is not None
    assert drafts[0].agent.tools_called.get("write_page") == 1
    assert drafts[0].agent.stop_reason == "end_turn"


async def test_write_pages_records_tools_called_per_page() -> None:
    """A page that calls multiple tools before write_page records each."""
    plan = _plan("index")
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(tool_uses=[("find_by_name", {"name": "Run", "top_k": 5})])
    fake.queue_tool_turn(tool_uses=[("read_node_by_qn", {"qualified_name": "pkg.Run"})])
    fake.queue_tool_turn(tool_uses=[("write_page", {"markdown": "# Page\n\nbody"})])
    fake.queue_tool_turn(text="")

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    assert drafts[0].agent is not None
    assert drafts[0].agent.tools_called["find_by_name"] == 1
    assert drafts[0].agent.tools_called["read_node_by_qn"] == 1
    assert drafts[0].agent.tools_called["write_page"] == 1
    assert drafts[0].agent.turns_used >= 3


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


async def test_write_pages_isolates_per_page_failures() -> None:
    """One failing page shouldn't poison the rest."""
    plan = _plan("index", "architecture", "getting-started")

    class _PartialFailure:
        """First + third pages succeed, second raises."""

        model = "fake-fail-v1"
        _idx = 0

        async def complete_text(self, *, system, blocks, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_with_tools(self, **_kwargs):
            self._idx += 1
            if self._idx == 2:
                raise StructuredCompletionError("forced page failure")
            tool_dispatch = _kwargs["tool_dispatch"]
            await tool_dispatch(
                "write_page", {"markdown": f"# page-{self._idx}\n\nbody"}
            )
            return ToolUseResult(stop_reason="end_turn", turns_used=1)

    drafts, failures = await write_pages(
        llm=_PartialFailure(),  # type: ignore[arg-type]
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(write_concurrency=1),  # serial → deterministic
    )

    assert sorted(failures) == ["architecture"]
    assert [d.slug for d in drafts] == ["index", "getting-started"]


async def test_write_pages_treats_empty_capture_as_failure() -> None:
    """If the agent never calls write_page and final_text is empty, fail."""
    plan = _plan("index")
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(text="   ")  # whitespace-only end_turn

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert drafts == []
    assert failures == ["index"]


async def test_write_pages_continues_when_retrieval_raises() -> None:
    """Retrieval failure must not abort the page — agent runs with empty bundle."""
    plan = _plan("index")
    failing_retriever = AsyncMock()
    failing_retriever.for_page.side_effect = RuntimeError("retrieval down")
    failing_retriever.hybrid = object()
    failing_retriever.embedder = None

    fake = FakeStructuredProvider()
    fake.queue_tool_turn(tool_uses=[("write_page", {"markdown": "# Overview\n\nbody"})])
    fake.queue_tool_turn(text="")

    drafts, failures = await write_pages(
        llm=fake,
        retriever=failing_retriever,
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    assert len(drafts) == 1
    assert drafts[0].slug == "index"


# ---------------------------------------------------------------------------
# Repair pass
# ---------------------------------------------------------------------------


class _StubResolver:
    """Resolver double for the repair pass."""

    def __init__(self, misses_per_call: list[list[str]]):
        self._queue = list(misses_per_call)
        self.calls = 0

    async def prevalidate_page(self, *, session, repository_id, markdown) -> list[str]:
        self.calls += 1
        if not self._queue:
            return []
        return self._queue.pop(0)


async def test_write_pages_fires_single_repair_on_unknown_identifiers() -> None:
    """First draft references an unknown identifier; the agent re-runs with
    a repair user block and the cleaned body wins."""
    plan = _plan("index")
    fake = FakeStructuredProvider()
    # First loop: write_page with bad identifier.
    fake.queue_tool_turn(
        tool_uses=[
            (
                "write_page",
                {
                    "markdown": "# Overview\n\nUses [[node:does.not.exist]] which is bogus.",
                },
            )
        ]
    )
    fake.queue_tool_turn(text="")
    # Repair loop: clean body.
    fake.queue_tool_turn(
        tool_uses=[
            (
                "write_page",
                {"markdown": "# Overview\n\nLanding without unknown placeholders."},
            )
        ]
    )
    fake.queue_tool_turn(text="")

    resolver = _StubResolver(misses_per_call=[["node:does.not.exist"], []])

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
        resolver=resolver,  # type: ignore[arg-type]
    )

    assert failures == []
    assert len(drafts) == 1
    # T3: gate caught the unverified citation, repair pass produced a
    # clean body, gate re-validates and passes.
    agent = drafts[0].agent
    assert agent is not None
    from backend.app.wiki.schemas import QualityStatus

    assert agent.quality_status == QualityStatus.PARTIAL
    assert agent.repair_attempts == 1
    assert agent.invalid_citations_stripped == 0
    # Repaired body shipped, not the original.
    assert drafts[0].body_md == "# Overview\n\nLanding without unknown placeholders."


async def test_write_pages_skips_repair_when_no_misses() -> None:
    """Clean first draft → T3 gate sees no `[[…]]` placeholders → no
    repair loop, telemetry quality_status=ok."""
    plan = _plan("index")
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": "# Overview\n\nClean body."})]
    )
    fake.queue_tool_turn(text="")

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    assert drafts[0].body_md == "# Overview\n\nClean body."
    # T3 telemetry: clean draft → no repair attempts.
    agent = drafts[0].agent
    assert agent is not None
    assert agent.repair_attempts == 0
    assert agent.invalid_citations_stripped == 0
    assert agent.citation_count == 0


async def test_write_pages_keeps_original_when_repair_fails() -> None:
    """If the repair LLM errors out, T3 strips the unverified citation
    and ships at quality_status=degraded — the page still ships, the
    run does NOT fail."""
    plan = _plan("index")

    class _RepairFails:
        model = "fake-repair-fail-v1"
        _idx = 0

        async def complete_text(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_with_tools(self, **_kwargs):
            self._idx += 1
            tool_dispatch = _kwargs["tool_dispatch"]
            if self._idx == 1:
                await tool_dispatch(
                    "write_page",
                    {"markdown": "# Overview\n\nUses [[node:does.not.exist]]."},
                )
                return ToolUseResult(stop_reason="end_turn", turns_used=1)
            raise StructuredCompletionError("repair LLM down")

    llm = _RepairFails()

    drafts, failures = await write_pages(
        llm=llm,  # type: ignore[arg-type]
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    # Final body has the unverified `[[node:…]]` stripped to inline
    # code so it renders without an unresolved chip. The page ships.
    assert drafts[0].body_md == "# Overview\n\nUses `does.not.exist`."
    agent = drafts[0].agent
    assert agent is not None
    from backend.app.wiki.schemas import QualityStatus

    assert agent.quality_status == QualityStatus.DEGRADED
    assert agent.invalid_citations_stripped == 1
    assert agent.repair_attempts == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_write_pages_respects_write_concurrency() -> None:
    """At most `write_concurrency` agent loops run concurrently."""
    plan = _plan("index", "architecture", "getting-started", "api", "deep-dive")

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    class _ProbeProvider:
        model = "probe-v1"

        async def complete_text(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_with_tools(self, **_kwargs):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.005)
            async with lock:
                in_flight -= 1
            tool_dispatch = _kwargs["tool_dispatch"]
            await tool_dispatch("write_page", {"markdown": "# page\n\nbody"})
            return ToolUseResult(stop_reason="end_turn", turns_used=1)

    drafts, failures = await write_pages(
        llm=_ProbeProvider(),  # type: ignore[arg-type]
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(write_concurrency=2),
    )
    assert failures == []
    assert len(drafts) == 5
    assert peak <= 2


# ---------------------------------------------------------------------------
# Budget exhaustion
# ---------------------------------------------------------------------------


async def test_write_pages_ships_partial_body_on_budget_exhaustion() -> None:
    """Hard cap exhaustion → ship whatever was last captured by write_page."""
    plan = _plan("index")

    class _RunsForever:
        model = "forever-v1"

        async def complete_text(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_json(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def complete_with_tools(self, **_kwargs):
            tool_dispatch = _kwargs["tool_dispatch"]
            # Capture early, then emulate a runaway loop that hits the cap.
            await tool_dispatch("write_page", {"markdown": "# Page\n\nearly body"})
            return ToolUseResult(
                stop_reason="budget_exhausted", turns_used=20, final_text=""
            )

    drafts, failures = await write_pages(
        llm=_RunsForever(),  # type: ignore[arg-type]
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    assert drafts[0].body_md == "# Page\n\nearly body"
    assert drafts[0].agent is not None
    assert drafts[0].agent.stop_reason == "budget_exhausted"


# ---------------------------------------------------------------------------
# T4 — coverage gate (top-level write_pages wiring)
# ---------------------------------------------------------------------------


async def test_write_pages_coverage_strips_open_questions_when_no_covers() -> None:
    """A page with no covers_questions skips the gate, so even a
    `## Open questions` block sails through. Confirms the gate is
    short-circuited when there's no contract to enforce."""
    from backend.app.wiki.schemas import QualityStatus

    plan = _plan("index")
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(
        tool_uses=[
            (
                "write_page",
                {
                    "markdown": (
                        "# Index\n\nLanding.\n\n## Open questions\n- intentional gap\n"
                    )
                },
            )
        ]
    )
    fake.queue_tool_turn(text="")

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    agent = drafts[0].agent
    assert agent is not None
    # Without covers_questions on the spec, the gate skips entirely.
    assert agent.coverage_repair_attempts == 0
    assert agent.quality_status == QualityStatus.OK


async def test_write_pages_skips_coverage_gate_when_no_covers_questions() -> None:
    """A page with empty `covers_questions` (e.g. the index landing
    page) should bypass the gate entirely and stay at quality_status=ok."""
    from backend.app.wiki.schemas import QualityStatus

    plan = _plan("index")  # No covers_questions on these specs.
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(
        tool_uses=[("write_page", {"markdown": "# Index\n\nlanding tour."})]
    )
    fake.queue_tool_turn(text="")

    drafts, failures = await write_pages(
        llm=fake,
        retriever=_stub_retriever(),
        session=None,  # type: ignore[arg-type]
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        context=_ctx(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        plan=plan,
        config=WikiGenerationConfig(),
    )
    assert failures == []
    agent = drafts[0].agent
    assert agent is not None
    assert agent.answered_questions == []
    assert agent.missing_questions == []
    assert agent.coverage_repair_attempts == 0
    assert agent.quality_status == QualityStatus.OK
