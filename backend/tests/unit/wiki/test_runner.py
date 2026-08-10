"""`LLMWikiGenerator` adapter contract.

The adapter is the single construction site between the sync processor and
`run_wiki_generation`: processor → `generate(...)` → `run_wiki_generation(...)`.
This pins the last hop so a refactor can't silently drop an argument or
mis-map the result shape the processor depends on.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

import backend.app.wiki.runner as runner_module
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.runner import LLMWikiGenerator
from backend.app.wiki.schemas import WikiGenerationResult

pytestmark = pytest.mark.asyncio


async def test_generate_forwards_core_args_and_maps_result(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> WikiGenerationResult:
        seen.update(kwargs)
        return WikiGenerationResult(
            run_id="run",
            repository_id=kwargs["repository_id"],
            source_commit=kwargs["source_commit"],
            model="fake",
            pages_planned=0,
            pages_written=0,
            pages_persisted=3,
            pages_skipped=2,
            pages_orphaned_deleted=1,
            unresolved_placeholders_total=0,
            wall_clock_ms=0,
        )

    monkeypatch.setattr(runner_module, "run_wiki_generation", _fake_run)
    generator = LLMWikiGenerator(
        llm=FakeStructuredProvider(),
        retriever=None,  # type: ignore[arg-type]
    )

    repository_id = uuid4()
    result = await generator.generate(
        session=None,  # type: ignore[arg-type]
        repository_id=repository_id,
        verified_commit="abc123",
    )

    # The verified commit becomes the source commit; force_full is gone —
    # routine syncs are always incremental, with an adaptive full re-plan.
    assert seen["repository_id"] == repository_id
    assert seen["source_commit"] == "abc123"
    assert "force_full" not in seen
    # Result shape the processor's GENERATE_WIKI step consumes.
    assert result.generated_documents == 3
    assert result.skipped_documents == 2
    assert result.pruned_documents == 1


async def test_generate_skips_without_verified_commit() -> None:
    generator = LLMWikiGenerator(
        llm=FakeStructuredProvider(),
        retriever=None,  # type: ignore[arg-type]
    )

    result = await generator.generate(
        session=None,  # type: ignore[arg-type]
        repository_id=uuid4(),
        verified_commit=None,
    )
    assert result.generated_documents == 0
    assert result.skipped_documents == 0
