"""Adapter that exposes `run_wiki_generation` under the legacy generator contract.

The sync pipeline (`backend.app.pipeline.processor`) expects a `wiki_generator`
with a `.generate(session, repository_id, sync_run_id, verified_commit)` method
returning an object with `.generated_documents` and `.skipped_documents` ints.
This module wraps the LLM pipeline in that shape so the rip-and-replace touches
only the construction site, not the orchestrator.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.wiki.llm_client import StructuredCompletionProvider
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    run_wiki_generation,
)
from backend.app.wiki.retrieval import WikiRetrievalService

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class LLMWikiResult:
    """Adapter result with the same attrs the legacy `WikiGenerationResult` had."""

    generated_documents: int
    skipped_documents: int
    pruned_documents: int
    model: str
    # Pages whose new-run quality would have regressed the persisted
    # quality status; the persist layer kept the existing row's content
    # and `quality` JSON as-is. Surfaced for telemetry only.
    kept_for_quality_documents: int = 0
    # Kept for compatibility with tests that destructure the legacy shape;
    # always 0 in the new pipeline (no preview variant).
    preview_generated_documents: int = 0
    preview_skipped_documents: int = 0
    preview_pruned_documents: int = 0


class LLMWikiGenerator:
    """Drop-in replacement for the legacy `WikiGenerator` used by `RepoSyncProcessor`."""

    def __init__(
        self,
        *,
        llm: StructuredCompletionProvider,
        retriever: WikiRetrievalService,
        config: WikiGenerationConfig | None = None,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
        | None = None,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._config = config or WikiGenerationConfig()
        # Used by Stage 4 agent tools so each parallel page gets a fresh
        # AsyncSession per tool call. Without this, write_concurrency=4
        # shares the bound session and SQLAlchemy raises on overlapping
        # tool dispatch.
        self._session_factory = session_factory

    @property
    def model(self) -> str:
        return self._llm.model

    async def generate(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_run_id: UUID | None = None,
        verified_commit: str | None = None,
        checkout_path: Path | str | None = None,
        force_full: bool = False,
    ) -> LLMWikiResult:
        if not verified_commit:
            logger.warning(
                "LLMWikiGenerator: skipping run for repo=%s — no verified commit",
                repository_id,
            )
            return LLMWikiResult(
                generated_documents=0,
                skipped_documents=0,
                pruned_documents=0,
                model=self._llm.model,
            )

        result = await run_wiki_generation(
            session=session,
            repository_id=repository_id,
            source_commit=verified_commit,
            sync_run_id=sync_run_id,
            llm=self._llm,
            retriever=self._retriever,
            checkout_path=checkout_path,
            config=self._config,
            session_factory=self._session_factory,
            force_full=force_full,
        )
        return LLMWikiResult(
            generated_documents=result.pages_persisted,
            skipped_documents=result.pages_skipped,
            pruned_documents=result.pages_orphaned_deleted,
            kept_for_quality_documents=len(result.kept_for_quality_slugs),
            model=result.model,
        )
