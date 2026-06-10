"""`LLMWikiGenerator` adapter contract — the `force_full` plumbing.

The OWNER rebuild button travels: API → sync_run.wiki_rebuild_requested →
processor → `generate(force_full=…)` → `run_wiki_generation(force_full=…)`.
This pins the last hop so a refactor can't silently drop the flag.
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


async def test_generate_forwards_force_full(monkeypatch: Any) -> None:
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
            pages_persisted=0,
            pages_skipped=0,
            pages_orphaned_deleted=0,
            unresolved_placeholders_total=0,
            wall_clock_ms=0,
        )

    monkeypatch.setattr(runner_module, "run_wiki_generation", _fake_run)
    generator = LLMWikiGenerator(
        llm=FakeStructuredProvider(),
        retriever=None,  # type: ignore[arg-type]
    )

    await generator.generate(
        session=None,  # type: ignore[arg-type]
        repository_id=uuid4(),
        verified_commit="abc123",
        force_full=True,
    )
    assert seen["force_full"] is True

    await generator.generate(
        session=None,  # type: ignore[arg-type]
        repository_id=uuid4(),
        verified_commit="abc123",
    )
    assert seen["force_full"] is False
