"""Domain-concept-aware retrieval rerank (T6).

Pure helpers used by `retrieval.py` and `agent_tools.search_code` to apply
an additive boost to retrieval hits that mention a `DomainConcept` from
the repo's `BusinessContext`. Never filters; always additive.

Formula:

    final_score = score_norm + gamma * domain_match
    domain_match = max(dc.importance for dc in matched_concepts), capped at 1.0
    gamma = 0.10 if business_context.confidence in {high, medium}
    gamma = 0.03 if business_context.confidence == low

`score_norm` is the per-batch max-normalized retrieval score; this keeps
the boost on a comparable scale across hybrid stores (code vs docs vs
banks) where absolute RRF scores differ.

Match scope: `concept.name` plus simple aliases — original lowered, snake
case, kebab case. A concept is matched when any of its aliases appears as
a substring in the lowercased haystack composed of the chunk's `content`,
`file_path`, and `qualified_name`. Aliases shorter than 4 characters are
dropped to avoid spurious matches (e.g. "id", "io").
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.schemas import (
    BusinessContextConfidence,
    DomainConcept,
)

GAMMA_HIGH_OR_MEDIUM = 0.10
GAMMA_LOW = 0.03

_MIN_ALIAS_LEN = 4

_CAMEL_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY_2 = re.compile(r"([a-z\d])([A-Z])")


def _camel_to_snake(name: str) -> str:
    """Convert `AccountBalance`, `accountBalance`, `HTTPServer` → snake_case.

    Preserves runs of capitals (`HTTPServer` → `http_server`, not
    `h_t_t_p_server`).
    """
    if not name:
        return ""
    s = _CAMEL_BOUNDARY_1.sub(r"\1_\2", name)
    s = _CAMEL_BOUNDARY_2.sub(r"\1_\2", s)
    return s.lower()


def concept_aliases(name: str) -> set[str]:
    """Build the alias set for one concept name.

    Returns the lowercased original plus snake_case and kebab-case
    variants, filtered to entries of length >= _MIN_ALIAS_LEN.
    """
    raw = (name or "").strip()
    if not raw:
        return set()
    lower = raw.lower()
    snake = _camel_to_snake(raw)
    kebab = snake.replace("_", "-")
    aliases = {lower, snake, kebab}
    return {a for a in aliases if len(a) >= _MIN_ALIAS_LEN}


def domain_match_score(
    *,
    text: str,
    file_path: str = "",
    qualified_name: str = "",
    concepts: Iterable[DomainConcept],
) -> float:
    """Return max importance over concepts whose aliases appear in the haystack.

    The haystack is the lowercased concatenation of chunk content, file
    path, and qualified name. Returns 0.0 when no concept matches or
    when `concepts` is empty.
    """
    haystack_parts = [text or "", file_path or "", qualified_name or ""]
    haystack = " ".join(haystack_parts).lower()
    if not haystack:
        return 0.0
    best = 0.0
    for concept in concepts:
        importance = float(getattr(concept, "importance", 0.5) or 0.0)
        if importance <= 0.0:
            continue
        for alias in concept_aliases(concept.name):
            if alias and alias in haystack:
                if importance > best:
                    best = importance
                break
    return min(best, 1.0)


def gamma_for_confidence(confidence: BusinessContextConfidence | None) -> float:
    """Pick the boost weight γ from the BusinessContext confidence."""
    if confidence in (
        BusinessContextConfidence.HIGH,
        BusinessContextConfidence.MEDIUM,
    ):
        return GAMMA_HIGH_OR_MEDIUM
    return GAMMA_LOW


def apply_domain_rerank(
    hits: list[RetrievedChunk],
    *,
    concepts: list[DomainConcept] | None,
    confidence: BusinessContextConfidence | None,
) -> list[RetrievedChunk]:
    """Return hits with `score = score_norm + γ * domain_match`, re-sorted.

    No-ops on empty `hits` or empty `concepts`. Uses per-batch max-norm so
    γ is comparable across hybrid stores. Stamps `domain_match` and
    `domain_boost` into each hit's metadata for telemetry; preserves the
    raw RRF score in `original_score` so downstream callers can recover
    the unboosted ordering if needed.
    """
    if not hits or not concepts:
        return hits
    gamma = gamma_for_confidence(confidence)
    max_score = max((float(h.score) for h in hits), default=0.0)
    if max_score <= 0.0:
        max_score = 1.0
    boosted: list[RetrievedChunk] = []
    for hit in hits:
        match = domain_match_score(
            text=hit.content,
            file_path=str(hit.metadata.get("file_path", "")),
            qualified_name=str(hit.metadata.get("qualified_name", "")),
            concepts=concepts,
        )
        norm = float(hit.score) / max_score
        boost = gamma * match
        new_meta = dict(hit.metadata)
        new_meta["domain_match"] = round(match, 4)
        new_meta["domain_boost"] = round(boost, 4)
        new_meta["original_score"] = float(hit.score)
        boosted.append(replace(hit, score=norm + boost, metadata=new_meta))
    boosted.sort(key=lambda h: (-h.score, str(h.chunk_id)))
    return boosted


__all__ = [
    "GAMMA_HIGH_OR_MEDIUM",
    "GAMMA_LOW",
    "apply_domain_rerank",
    "concept_aliases",
    "domain_match_score",
    "gamma_for_confidence",
]
