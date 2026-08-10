"""Compute hard tier quotas + per-cluster cap from `RepoSignals`.

The page allocator (Stage 3, `plan_pages`) needs deterministic limits
on how many pages a `RepoSignals` snapshot can produce so the LLM
cannot over-build. The single source of truth lives here:

```
target_pages = 6
             + ceil(public_topics * 1.3)
             + ceil(supporting_topics * 0.4)

min_pages = 5
max_pages = 30
per-cluster cap = 4 pages
internal/test_scaffolding tier = 0 dedicated pages
dedicated page requires salience_score >= 0.65
   (exception: docs/CLI/public_api seeded → automatic dedicated)
```

For `go-oas3` (CLI code generator with ~5 public topics): the formula
gives ≈ 12-14 pages instead of ~25. That number is the "target" — the
planner should produce something in [target - 2, target + 2] and never
below `min_pages` or above `max_pages`.

Pure / deterministic. No LLM. No hidden state.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from backend.app.wiki.schemas import (
    CandidateKind,
    RepoSignals,
    SalienceTier,
    TopicCandidate,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_BASE_PAGES: Final[int] = 6
_PUBLIC_PAGE_RATIO: Final[float] = 1.3
_SUPPORTING_PAGE_RATIO: Final[float] = 0.4
_MIN_PAGES: Final[int] = 5
_MAX_PAGES: Final[int] = 30
_PER_CLUSTER_CAP: Final[int] = 4
_DEDICATED_PAGE_SCORE_FLOOR: Final[float] = 0.65

# These candidate kinds are ALWAYS allowed a dedicated page even if
# their salience_score is below the floor — the curated extraction
# layers (S2 cli_extractor, S3 docs_outline, manifest public_api) are
# already strong evidence that the topic is user-facing.
_AUTOMATIC_DEDICATED_KINDS: Final = frozenset(
    {
        CandidateKind.CLI_COMMAND,
        CandidateKind.DOCS_TOPIC,
        CandidateKind.PUBLIC_API,
    }
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TierQuotas:
    """Hard limits derived from `RepoSignals.topic_candidates`.

    `target_pages` is the soft target — the planner should hit
    [target - 2, target + 2]. `min_pages` and `max_pages` are the
    hard floor / ceiling and apply regardless of the signals.
    """

    target_pages: int
    min_pages: int = _MIN_PAGES
    max_pages: int = _MAX_PAGES
    per_cluster_cap: int = _PER_CLUSTER_CAP
    public_topic_count: int = 0
    supporting_topic_count: int = 0
    eligible_dedicated_count: int = 0


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def quotas_for(signals: RepoSignals) -> TierQuotas:
    """Compute hard tier quotas from a `RepoSignals` snapshot."""
    public_count = sum(
        1 for c in signals.topic_candidates if c.salience_tier == SalienceTier.PUBLIC
    )
    supporting_count = sum(
        1
        for c in signals.topic_candidates
        if c.salience_tier == SalienceTier.SUPPORTING
    )
    eligible = sum(
        1 for c in signals.topic_candidates if is_eligible_for_dedicated_page(c)
    )

    raw_target = (
        _BASE_PAGES
        + math.ceil(public_count * _PUBLIC_PAGE_RATIO)
        + math.ceil(supporting_count * _SUPPORTING_PAGE_RATIO)
    )
    target = max(_MIN_PAGES, min(_MAX_PAGES, raw_target))

    return TierQuotas(
        target_pages=target,
        public_topic_count=public_count,
        supporting_topic_count=supporting_count,
        eligible_dedicated_count=eligible,
    )


def is_eligible_for_dedicated_page(candidate: TopicCandidate) -> bool:
    """Return True iff the candidate may receive its own page.

    A `TopicCandidate` is eligible for a dedicated page when:
        - tier is PUBLIC (always); OR
        - tier is SUPPORTING and (kind is in the automatic-dedicated set
          OR salience_score >= 0.65); never for INTERNAL /
          TEST_SCAFFOLDING.

    INTERNAL / TEST_SCAFFOLDING tiers are NEVER eligible for dedicated
    pages — they appear as sections under public pages, never on their
    own.
    """
    if candidate.salience_tier in {
        SalienceTier.INTERNAL,
        SalienceTier.TEST_SCAFFOLDING,
    }:
        return False
    if candidate.salience_tier == SalienceTier.PUBLIC:
        return True
    # SUPPORTING tier: dedicated page only if score is above the floor
    # OR the candidate kind is auto-promoted (CLI/docs/public_api).
    if candidate.candidate_kind in _AUTOMATIC_DEDICATED_KINDS:
        return True
    return candidate.salience_score >= _DEDICATED_PAGE_SCORE_FLOOR


def cluster_caps(
    signals: RepoSignals,
    *,
    cap: int = _PER_CLUSTER_CAP,
) -> dict[str, int]:
    """Group eligible candidates by cluster prefix and return how many
    pages each cluster may contribute, capped at `cap`.

    Cluster prefix is the part of `normalized_key` before the first
    `:` separator (e.g. `cli`, `docs`, `pkg`, `internal`, `cmd`).
    Used by the planner to refuse > `cap` pages from the same cluster.
    """
    counts: dict[str, int] = defaultdict(int)
    for cand in signals.topic_candidates:
        if not is_eligible_for_dedicated_page(cand):
            continue
        prefix = _cluster_prefix(cand.normalized_key)
        counts[prefix] += 1
    return {key: min(value, cap) for key, value in counts.items()}


def select_eligible(signals: RepoSignals) -> list[TopicCandidate]:
    """Filter `topic_candidates` down to those eligible for dedicated
    pages. Sorted by descending salience_score for stable downstream
    behavior."""
    eligible = [
        c for c in signals.topic_candidates if is_eligible_for_dedicated_page(c)
    ]
    eligible.sort(key=lambda c: (-c.salience_score, c.normalized_key))
    return eligible


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _cluster_prefix(normalized_key: str) -> str:
    if ":" in normalized_key:
        return normalized_key.split(":", 1)[0]
    return normalized_key


__all__ = (
    "TierQuotas",
    "cluster_caps",
    "is_eligible_for_dedicated_page",
    "quotas_for",
    "select_eligible",
)
