"""LLM-driven wiki generation pipeline.

Replacement for `backend/app/wiki/{generator,planner}{,_v2,_v3}.py`. Keeps AST/RAG
retrievers as a context source for prompts; lets the LLM decide page structure.

Public surface:
    - run_wiki_generation: end-to-end pipeline entry point
    - LLMWikiGenerator: adapter wrapping run_wiki_generation under the legacy
      `wiki_generator.generate(...)` contract used by `RepoSyncProcessor`
    - WikiQueryService: read-side facade for api/wiki.py and mcp/resources.py
"""

from __future__ import annotations

from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    run_wiki_generation,
)
from backend.app.wiki.queries import (
    WikiCitation,
    WikiPage,
    WikiQueryService,
    WikiRelatedNode,
    WikiTreeNode,
)
from backend.app.wiki.runner import LLMWikiGenerator, LLMWikiResult
from backend.app.wiki.schemas import WikiGenerationResult

__all__ = [
    "LLMWikiGenerator",
    "LLMWikiResult",
    "WikiCitation",
    "WikiGenerationConfig",
    "WikiGenerationResult",
    "WikiPage",
    "WikiQueryService",
    "WikiRelatedNode",
    "WikiTreeNode",
    "run_wiki_generation",
]
