"""Tests for Stage 1.5 — `generate_mindmap` and the mindmap-aware repo block."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from backend.app.wiki.context import RepoContext
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.pipeline import generate_mindmap
from backend.app.wiki.prompts import (
    MINDMAP_GENERATOR_SYSTEM,
    build_mindmap_user,
    build_repo_context_block,
)
from backend.app.wiki.schemas import (
    MindMap,
    MindMapFlow,
    MindMapModule,
    RepoOverview,
)


def _ctx(*, mindmap: MindMap | None = None) -> RepoContext:
    return RepoContext(
        repository_id=UUID("00000000-0000-0000-0000-000000000abc"),
        commit_sha="cafef00d",
        file_tree_hash="a" * 64,
        docs_hash="b" * 64,
        summaries_hash="c" * 64,
        identity_hash="d" * 64,
        mindmap=mindmap,
    )


def test_repo_context_block_omits_mindmap_when_absent() -> None:
    """Byte-equality is critical: stages 2-4 share a provider prompt-cache
    block, and we must not perturb the layout for runs without a mindmap."""
    block = build_repo_context_block(_ctx())
    assert "<mindmap>" not in block
    # Trailer is unchanged.
    assert block.rstrip().endswith("</repo_context>")


def test_repo_context_block_renders_mindmap_when_present() -> None:
    mindmap = MindMap(
        root_concept="A test repo for unit fixtures.",
        layered_modules=[
            MindMapModule(
                name="src/main",
                role="Entry-point wiring",
                children=[
                    MindMapModule(name="src/main/run", role="Top-level run loop"),
                ],
            ),
        ],
        entry_points=["src.main.run", "src/cli.py"],
        key_flows=[
            MindMapFlow(
                label="Indexing flow",
                steps=["read repo", "build graph", "persist"],
            ),
        ],
    )
    block = build_repo_context_block(_ctx(mindmap=mindmap))
    assert "<mindmap>" in block
    assert "<root_concept>A test repo for unit fixtures.</root_concept>" in block
    assert "src/main: Entry-point wiring" in block
    assert "  - src/main/run: Top-level run loop" in block
    assert "<entry_points>" in block
    assert "- src.main.run" in block
    assert "<key_flows>" in block
    assert "- Indexing flow" in block
    assert "    > read repo" in block


def test_build_mindmap_user_includes_overview_and_schema_hint() -> None:
    overview = RepoOverview(
        one_line="Tiny test fixture.",
        long_description="Used only in unit tests.",
        primary_languages=["python"],
    )
    body = build_mindmap_user(context=_ctx(), overview=overview)
    assert "<repo_overview>" in body
    assert '"one_line": "Tiny test fixture."' in body
    assert "MindMap" in body  # schema hint mentions the schema name
    assert "root_concept" in body
    assert "key_flows" in body


@pytest.mark.asyncio
async def test_generate_mindmap_parses_clean_json() -> None:
    fake = FakeStructuredProvider()
    payload = MindMap(
        root_concept="Self-hosted code-knowledge tool.",
        layered_modules=[MindMapModule(name="backend", role="API + pipeline")],
        entry_points=["backend.app.cli"],
        key_flows=[MindMapFlow(label="ingest", steps=["clone", "index", "embed"])],
    )
    fake.queue(payload.model_dump_json())
    overview = RepoOverview(one_line="x", long_description="...")
    result = await generate_mindmap(
        llm=fake, context=_ctx(), overview=overview
    )
    assert result.root_concept == "Self-hosted code-knowledge tool."
    assert [m.name for m in result.layered_modules] == ["backend"]
    assert result.entry_points == ["backend.app.cli"]
    assert len(fake.calls) == 1
    # System prompt must be the canonical MINDMAP one (cache-stable).
    assert fake.calls[0]["system"] == MINDMAP_GENERATOR_SYSTEM


@pytest.mark.asyncio
async def test_generate_mindmap_retries_once_on_bad_json() -> None:
    fake = FakeStructuredProvider()
    fake.queue("not json at all")
    fake.queue(MindMap(root_concept="recovered").model_dump_json())
    overview = RepoOverview(one_line="x", long_description="...")
    result = await generate_mindmap(
        llm=fake, context=_ctx(), overview=overview
    )
    assert result.root_concept == "recovered"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_generate_mindmap_returns_empty_after_two_failures() -> None:
    """Failure must be non-fatal — an empty MindMap ships and downstream
    stages keep working without orientation hints."""
    fake = FakeStructuredProvider()
    fake.queue("garbage 1")
    fake.queue("garbage 2")
    overview = RepoOverview(one_line="x", long_description="...")
    result = await generate_mindmap(
        llm=fake, context=_ctx(), overview=overview
    )
    assert result == MindMap()
    assert result.root_concept == ""
    assert result.layered_modules == []
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_generate_mindmap_uses_cached_repo_context_block() -> None:
    """The first block must be the cacheable repo-context block — that's
    what makes provider prompt caching survive across stages."""
    fake = FakeStructuredProvider()
    fake.queue(MindMap().model_dump_json())
    overview = RepoOverview(one_line="x", long_description="...")
    await generate_mindmap(llm=fake, context=_ctx(), overview=overview)
    blocks = fake.calls[0]["blocks"]
    assert blocks[0][1] is True  # first block is cacheable
    assert "<repo_context>" in blocks[0][0]
    assert blocks[1][1] is False  # second block (user) is fresh


def test_generate_mindmap_flow_round_trips_through_run_stages_1_to_3(
) -> None:
    """Sanity: a populated MindMap round-trips through model_dump_json /
    model_validate_json. Guards against silent schema drift on the
    self-referential `MindMapModule`.
    """
    mindmap = MindMap(
        root_concept="r",
        layered_modules=[
            MindMapModule(
                name="a",
                role="x",
                children=[
                    MindMapModule(
                        name="a/b",
                        role="y",
                        children=[MindMapModule(name="a/b/c", role="z")],
                    )
                ],
            )
        ],
    )
    raw = mindmap.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["layered_modules"][0]["children"][0]["children"][0]["name"] == "a/b/c"
    assert MindMap.model_validate_json(raw) == mindmap
