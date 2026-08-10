"""Mindmap / plan redundancy telemetry (T7).

After `plan_pages` produces a `PagePlan`, this module computes pairwise
question-set Jaccard plus purpose-text cosine similarity over page
embeddings, and flags suspicious overlaps. Output is a
`WikiPlanQualityReport` that surfaces in the admin/quality dashboard;
**it never blocks publish and never auto-merges**.

Suspicious threshold:

    question_jaccard   = |Q_a ∩ Q_b| / |Q_a ∪ Q_b|
    purpose_similarity = cosine(embed(purpose_a), embed(purpose_b))
    suspicious         = question_jaccard >= 0.5 AND
                         purpose_similarity >= 0.82

Both halves are required — two pages on the same broad topic but with
disjoint `covers_questions` are complementary, not redundant; two pages
that share questions but spell out very different concerns are also
fine. Only when *both* signals fire do we surface the pair.
"""

from __future__ import annotations

import logging
import math

from backend.app.llm.embedder import EmbedProvider
from backend.app.wiki.schemas import (
    OverlapPair,
    PagePlan,
    PageSpec,
    WikiPlanQualityReport,
)

logger = logging.getLogger(__name__)

JACCARD_THRESHOLD = 0.5
COSINE_THRESHOLD = 0.82


def question_jaccard(a: PageSpec, b: PageSpec) -> float:
    """Set Jaccard over `covers_questions`; 0.0 when both are empty."""
    qs_a = {q.value for q in a.covers_questions}
    qs_b = {q.value for q in b.covers_questions}
    if not qs_a and not qs_b:
        return 0.0
    union = qs_a | qs_b
    if not union:
        return 0.0
    return len(qs_a & qs_b) / len(union)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine; returns 0.0 when either side is the zero vector."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _purpose_text(spec: PageSpec) -> str:
    title = (spec.title or "").strip()
    purpose = (spec.purpose or "").strip()
    if title and purpose:
        return f"{title}: {purpose}"
    return purpose or title or spec.slug


async def analyze_plan_quality(
    *,
    plan: PagePlan,
    embedder: EmbedProvider | None,
    jaccard_threshold: float = JACCARD_THRESHOLD,
    cosine_threshold: float = COSINE_THRESHOLD,
) -> WikiPlanQualityReport:
    """Pairwise overlap analysis over `plan.pages`.

    Returns a report with sorted `suspicious_pairs`. When `embedder` is
    None we never flag (the AND-gate requires both signals); the caller
    can still inspect `question_jaccard` separately by re-running with
    a stub embedder if curiosity strikes.

    Embedding failures degrade gracefully — we log a warning and return
    an empty report.
    """
    pages = plan.pages
    if len(pages) < 2:
        return WikiPlanQualityReport()
    if embedder is None:
        return WikiPlanQualityReport()

    purposes = [_purpose_text(p) for p in pages]
    try:
        embeddings = await embedder.embed(purposes)
    except Exception as exc:  # pragma: no cover — exercised in integration runs
        logger.warning(
            "analyze_plan_quality: embedding call failed (%s); returning empty report",
            exc,
        )
        return WikiPlanQualityReport()

    suspicious: list[OverlapPair] = []
    for i, page_a in enumerate(pages):
        for j in range(i + 1, len(pages)):
            page_b = pages[j]
            jac = question_jaccard(page_a, page_b)
            cos = cosine_similarity(embeddings[i], embeddings[j])
            if jac >= jaccard_threshold and cos >= cosine_threshold:
                slug_a, slug_b = sorted([page_a.slug, page_b.slug])
                suspicious.append(
                    OverlapPair(
                        slug_a=slug_a,
                        slug_b=slug_b,
                        question_jaccard=round(jac, 4),
                        purpose_similarity=round(cos, 4),
                    )
                )

    suspicious.sort(
        key=lambda p: (
            -p.question_jaccard,
            -p.purpose_similarity,
            p.slug_a,
            p.slug_b,
        )
    )
    return WikiPlanQualityReport(suspicious_pairs=suspicious)


__all__ = [
    "COSINE_THRESHOLD",
    "JACCARD_THRESHOLD",
    "analyze_plan_quality",
    "cosine_similarity",
    "question_jaccard",
]
