"""Smoke test: every PR 1 module imports cleanly and exports its declared types.

Real behavior tests land in PR 2-4 alongside the implementations.
"""

from __future__ import annotations


def test_imports() -> None:
    from backend.app.wiki import (
        citations,
        context,
        llm_client,
        pipeline,
        prompts,
        queries,
        retrieval,
        schemas,
        store,
    )

    assert citations is not None
    assert context is not None
    assert llm_client is not None
    assert pipeline is not None
    assert prompts is not None
    assert queries is not None
    assert retrieval is not None
    assert schemas is not None
    assert store is not None


def test_schemas_round_trip() -> None:
    """Pydantic models are real, not stubs. Round-trip the public ones."""
    from backend.app.wiki.schemas import (
        EntryPoint,
        KeyConcept,
        ModuleNote,
        PagePlan,
        PageSpec,
        RepoOverview,
    )

    overview = RepoOverview(
        one_line="A test repo",
        long_description="A small fixture for unit tests.",
        primary_languages=["python"],
        primary_audiences=["library consumers"],
        entry_points=[EntryPoint(file_path="src/main.py", why="entry")],
        key_concepts=[KeyConcept(name="Pipeline", definition="ordered stages")],
        notable_modules=[ModuleNote(path="src/", role="application code")],
        open_questions=["unclear x"],
    )
    assert RepoOverview.model_validate_json(overview.model_dump_json()) == overview

    plan = PagePlan(
        pages=[
            PageSpec(slug="index", title="Overview", purpose="landing"),
            PageSpec(
                slug="architecture",
                title="Architecture",
                purpose="design overview",
                sources_hint=["src/pipeline.py"],
                diagram=True,
            ),
        ]
    )
    assert PagePlan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.pages[0].slug == "index"


def test_placeholder_regex() -> None:
    """The placeholder regex matches the two documented kinds (node, doc)."""
    from backend.app.wiki.citations import PLACEHOLDER_RE

    matches = PLACEHOLDER_RE.findall(
        "see [[node:foo.bar.Baz]] and [[file:src/x.py]] and [[doc:README.md#intro]]"
    )
    # `[[file:…]]` is no longer a citation kind; only node + doc match.
    assert matches == [
        ("node", "foo.bar.Baz"),
        ("doc", "README.md#intro"),
    ]


def test_pipeline_entry_point_is_callable() -> None:
    """The orchestrator is exposed and its config has the documented defaults."""
    import inspect

    from backend.app.wiki.pipeline import (
        WikiGenerationConfig,
        run_wiki_generation,
    )

    config = WikiGenerationConfig()
    assert config.write_concurrency == 4
    assert config.persist is True
    assert inspect.iscoroutinefunction(run_wiki_generation)


def test_fake_structured_provider() -> None:
    """The test fake works end-to-end without network."""
    import asyncio

    from backend.app.wiki.llm_client import CacheBlock, FakeStructuredProvider
    from backend.app.wiki.schemas import RepoOverview

    fake = FakeStructuredProvider()
    fake.queue('{"one_line":"x","long_description":"y"}')

    async def _call() -> RepoOverview:
        return await fake.complete_json(
            system="sys",
            blocks=[CacheBlock(text="ctx", cacheable=True)],
            schema=RepoOverview,
        )

    result = asyncio.run(_call())
    assert result.one_line == "x"
    assert result.long_description == "y"
    assert result.primary_languages == []
