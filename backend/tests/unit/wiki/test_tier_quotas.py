"""Tests for `tier_quotas` — deterministic page-count limits.

Pins the public/supporting/internal weights so a future tweak can't
silently shift go-oas3-shaped repos out of the 12-16 page band again.
"""

from __future__ import annotations

from backend.app.wiki.schemas import (
    CandidateKind,
    RepoSignals,
    SalienceTier,
    TopicCandidate,
)
from backend.app.wiki.tier_quotas import (
    cluster_caps,
    is_eligible_for_dedicated_page,
    quotas_for,
    select_eligible,
)


def _candidate(
    *,
    key: str,
    tier: SalienceTier = SalienceTier.PUBLIC,
    score: float = 0.8,
    kind: CandidateKind = CandidateKind.MODULE_CLUSTER,
) -> TopicCandidate:
    return TopicCandidate(
        id=key,
        title=key,
        normalized_key=key,
        salience_score=score,
        salience_tier=tier,
        candidate_kind=kind,
    )


def _signals(*candidates: TopicCandidate) -> RepoSignals:
    return RepoSignals(topic_candidates=list(candidates))


# ---------------------------------------------------------------------------
# Quota math
# ---------------------------------------------------------------------------


def test_target_pages_for_go_oas3_shape_lands_in_band():
    """5 public + 4 supporting → target ≈ 14 (within the 12-16 band)."""
    sigs = _signals(
        *[
            _candidate(key=f"cli:tool/cmd{i}", tier=SalienceTier.PUBLIC, score=1.0)
            for i in range(5)
        ],
        *[
            _candidate(key=f"pkg:helper{i}", tier=SalienceTier.SUPPORTING, score=0.50)
            for i in range(4)
        ],
    )
    q = quotas_for(sigs)
    assert q.target_pages == 6 + 7 + 2  # 6 + ceil(5*1.3)=7 + ceil(4*0.4)=2
    assert q.target_pages == 15
    assert 12 <= q.target_pages <= 16
    assert q.public_topic_count == 5
    assert q.supporting_topic_count == 4


def test_min_max_clamps_apply():
    sigs_zero = _signals()
    q_zero = quotas_for(sigs_zero)
    assert q_zero.target_pages == 6  # base, above the 5-page floor
    assert q_zero.min_pages == 5
    assert q_zero.max_pages == 30

    sigs_huge = _signals(
        *[
            _candidate(key=f"x{i}", tier=SalienceTier.PUBLIC, score=1.0)
            for i in range(50)
        ],
    )
    q_huge = quotas_for(sigs_huge)
    assert q_huge.target_pages == 30  # clamped at max


def test_internal_and_test_scaffolding_do_not_inflate_target():
    sigs = _signals(
        *[_candidate(key=f"i{i}", tier=SalienceTier.INTERNAL) for i in range(20)],
        *[
            _candidate(key=f"t{i}", tier=SalienceTier.TEST_SCAFFOLDING)
            for i in range(20)
        ],
    )
    q = quotas_for(sigs)
    assert q.public_topic_count == 0
    assert q.supporting_topic_count == 0
    assert q.target_pages == 6


def test_per_cluster_cap_constant_is_4():
    q = quotas_for(_signals())
    assert q.per_cluster_cap == 4


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_public_tier_is_always_eligible():
    cand = _candidate(key="x", tier=SalienceTier.PUBLIC, score=0.0)
    assert is_eligible_for_dedicated_page(cand) is True


def test_internal_and_test_scaffolding_never_eligible():
    for tier in (SalienceTier.INTERNAL, SalienceTier.TEST_SCAFFOLDING):
        cand = _candidate(key="x", tier=tier, score=1.0)
        assert is_eligible_for_dedicated_page(cand) is False


def test_supporting_tier_needs_score_above_floor_or_curated_kind():
    low = _candidate(
        key="pkg:helper",
        tier=SalienceTier.SUPPORTING,
        score=0.50,
        kind=CandidateKind.MODULE_CLUSTER,
    )
    high = _candidate(
        key="pkg:helper",
        tier=SalienceTier.SUPPORTING,
        score=0.70,
        kind=CandidateKind.MODULE_CLUSTER,
    )
    assert is_eligible_for_dedicated_page(low) is False
    assert is_eligible_for_dedicated_page(high) is True


def test_curated_kinds_are_eligible_at_supporting_tier_regardless_of_score():
    """CLI commands, doc topics, and public_api seeds get auto-promoted
    even when their salience_score is below the dedicated-page floor."""
    for kind in (
        CandidateKind.CLI_COMMAND,
        CandidateKind.DOCS_TOPIC,
        CandidateKind.PUBLIC_API,
    ):
        cand = _candidate(key="x", tier=SalienceTier.SUPPORTING, score=0.10, kind=kind)
        assert is_eligible_for_dedicated_page(cand) is True


# ---------------------------------------------------------------------------
# Cluster caps + selection
# ---------------------------------------------------------------------------


def test_cluster_caps_groups_by_prefix_and_caps_at_4():
    sigs = _signals(
        *[
            _candidate(
                key=f"pkg:foo{i}",
                tier=SalienceTier.PUBLIC,
                score=1.0,
            )
            for i in range(7)
        ],
        _candidate(key="cli:tool/run", tier=SalienceTier.PUBLIC, score=1.0),
    )
    caps = cluster_caps(sigs)
    assert caps["pkg"] == 4  # 7 candidates capped at 4
    assert caps["cli"] == 1


def test_select_eligible_returns_only_eligible_sorted_by_score_desc():
    a = _candidate(key="a:1", tier=SalienceTier.PUBLIC, score=0.95)
    b = _candidate(key="b:1", tier=SalienceTier.PUBLIC, score=0.65)
    c_internal = _candidate(key="c:1", tier=SalienceTier.INTERNAL, score=1.0)
    sigs = _signals(b, c_internal, a)
    eligible = select_eligible(sigs)
    assert [c.normalized_key for c in eligible] == ["a:1", "b:1"]


def test_eligible_dedicated_count_reflects_eligibility():
    sigs = _signals(
        _candidate(key="cli:tool", tier=SalienceTier.PUBLIC),
        _candidate(
            key="docs:foo",
            tier=SalienceTier.SUPPORTING,
            score=0.10,
            kind=CandidateKind.DOCS_TOPIC,
        ),
        _candidate(
            key="pkg:helper",
            tier=SalienceTier.SUPPORTING,
            score=0.50,
            kind=CandidateKind.MODULE_CLUSTER,
        ),
    )
    q = quotas_for(sigs)
    assert q.eligible_dedicated_count == 2  # PUBLIC + curated DOCS_TOPIC
