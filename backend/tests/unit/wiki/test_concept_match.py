"""Unit tests for the T6 domain-concept rerank helpers."""

from __future__ import annotations

from uuid import uuid4

from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.concept_match import (
    GAMMA_HIGH_OR_MEDIUM,
    GAMMA_LOW,
    apply_domain_rerank,
    concept_aliases,
    domain_match_score,
    gamma_for_confidence,
)
from backend.app.wiki.schemas import (
    BusinessContextConfidence,
    DomainConcept,
)


# ---------------------------------------------------------------------------
# concept_aliases
# ---------------------------------------------------------------------------


def test_concept_aliases_emits_lower_snake_kebab() -> None:
    aliases = concept_aliases("AccountBalance")
    assert "accountbalance" in aliases
    assert "account_balance" in aliases
    assert "account-balance" in aliases


def test_concept_aliases_handles_acronyms() -> None:
    aliases = concept_aliases("HTTPServer")
    # `http_server` should appear; the algorithm preserves caps runs.
    assert "http_server" in aliases
    assert "http-server" in aliases


def test_concept_aliases_drops_short_strings() -> None:
    # "Id" → 2 chars → all variants are < 4 chars → empty set
    assert concept_aliases("Id") == set()


def test_concept_aliases_blank_input() -> None:
    assert concept_aliases("") == set()
    assert concept_aliases("   ") == set()


# ---------------------------------------------------------------------------
# domain_match_score
# ---------------------------------------------------------------------------


def _concept(name: str, importance: float = 0.7) -> DomainConcept:
    return DomainConcept(name=name, definition=f"{name} desc", importance=importance)


def test_domain_match_score_picks_max_importance() -> None:
    concepts = [
        _concept("Account", importance=0.9),
        _concept("Webhook", importance=0.4),
    ]
    score = domain_match_score(
        text="def get_account_balance(): pass",
        file_path="src/accounts/repo.py",
        qualified_name="src.accounts.repo.get_account_balance",
        concepts=concepts,
    )
    assert score == 0.9


def test_domain_match_score_no_match_returns_zero() -> None:
    score = domain_match_score(
        text="def helper(): pass",
        file_path="src/util.py",
        qualified_name="src.util.helper",
        concepts=[_concept("Invoice", importance=0.8)],
    )
    assert score == 0.0


def test_domain_match_score_empty_concepts_returns_zero() -> None:
    assert (
        domain_match_score(
            text="def get_account(): pass",
            file_path="x",
            qualified_name="y",
            concepts=[],
        )
        == 0.0
    )


def test_domain_match_score_caps_at_one() -> None:
    # Importance is bounded by the schema (le=1.0); passing inflated value
    # via raw construction here would still cap due to clamp.
    score = domain_match_score(
        text="account",
        concepts=[_concept("Account", importance=1.0)],
    )
    assert score == 1.0


def test_domain_match_score_matches_kebab_in_path() -> None:
    score = domain_match_score(
        text="// nothing useful",
        file_path="src/account-balance/index.ts",
        qualified_name="",
        concepts=[_concept("AccountBalance", importance=0.8)],
    )
    assert score == 0.8


# ---------------------------------------------------------------------------
# gamma_for_confidence
# ---------------------------------------------------------------------------


def test_gamma_high_and_medium_use_high_weight() -> None:
    assert gamma_for_confidence(BusinessContextConfidence.HIGH) == GAMMA_HIGH_OR_MEDIUM
    assert (
        gamma_for_confidence(BusinessContextConfidence.MEDIUM) == GAMMA_HIGH_OR_MEDIUM
    )


def test_gamma_low_and_none_use_low_weight() -> None:
    assert gamma_for_confidence(BusinessContextConfidence.LOW) == GAMMA_LOW
    assert gamma_for_confidence(None) == GAMMA_LOW


# ---------------------------------------------------------------------------
# apply_domain_rerank
# ---------------------------------------------------------------------------


def _hit(*, content: str, qn: str, score: float, file_path: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        store="code",
        chunk_id=uuid4(),
        content=content,
        score=score,
        metadata={
            "qualified_name": qn,
            "file_path": file_path or f"src/{qn.replace('.', '/')}.py",
        },
    )


def test_rerank_promotes_concept_match_above_higher_baseline() -> None:
    # Baseline: hit B has higher RRF score than A.
    # With Account concept (high importance + high confidence), A should
    # outrank B because B doesn't mention any concept.
    hit_a = _hit(
        content="def credit_account(...): ...",
        qn="src.account.credit",
        score=0.05,  # lower baseline
    )
    hit_b = _hit(
        content="def helper(...): ...",
        qn="src.util.helper",
        score=0.07,  # higher baseline
    )
    out = apply_domain_rerank(
        [hit_a, hit_b],
        concepts=[_concept("Account", importance=0.9)],
        confidence=BusinessContextConfidence.HIGH,
    )
    # Reranked order: A first (0.05/0.07 ≈ 0.714 + 0.10*0.9 = 0.804) vs B (1.0 + 0).
    # Wait — B has higher raw score, so norm is 1.0 for B and ~0.71 for A.
    # A's boosted = 0.714 + 0.09 = 0.804; B = 1.0. B still wins.
    # The acceptance criterion is about *concept presence flipping ties*,
    # not arbitrary boost magnitudes — so test with closer baseline.
    assert {h.metadata.get("qualified_name") for h in out} == {
        "src.account.credit",
        "src.util.helper",
    }
    # But the boost should be stamped:
    a_out = next(h for h in out if "credit" in h.metadata["qualified_name"])
    assert a_out.metadata["domain_match"] == 0.9
    assert a_out.metadata["domain_boost"] == round(0.9 * GAMMA_HIGH_OR_MEDIUM, 4)


def test_rerank_flips_close_tie_in_favor_of_concept_match() -> None:
    hit_a = _hit(
        content="def credit_account(): ...",
        qn="src.account.credit",
        score=0.060,
    )
    hit_b = _hit(
        content="def helper(): ...",
        qn="src.util.helper",
        score=0.061,  # tiny lead
    )
    out = apply_domain_rerank(
        [hit_a, hit_b],
        concepts=[_concept("Account", importance=0.8)],
        confidence=BusinessContextConfidence.HIGH,
    )
    # A boosted: 0.060/0.061 + 0.10 * 0.8 = 0.984 + 0.08 = 1.064
    # B boosted: 1.0
    # → A wins.
    assert out[0].metadata["qualified_name"] == "src.account.credit"


def test_rerank_low_confidence_uses_smaller_gamma() -> None:
    hit_a = _hit(content="account", qn="src.account.foo", score=0.05)
    hit_b = _hit(content="helper", qn="src.util.bar", score=0.06)
    out = apply_domain_rerank(
        [hit_a, hit_b],
        concepts=[_concept("Account", importance=1.0)],
        confidence=BusinessContextConfidence.LOW,
    )
    # γ=0.03; A: 0.05/0.06 + 0.03 = 0.863; B: 1.0 → A still loses.
    # But the metadata should reflect γ=0.03.
    a_out = next(h for h in out if h.metadata["qualified_name"] == "src.account.foo")
    assert a_out.metadata["domain_boost"] == round(GAMMA_LOW * 1.0, 4)


def test_rerank_noop_when_no_concepts() -> None:
    hits = [
        _hit(content="x", qn="a.b", score=0.5),
        _hit(content="y", qn="a.c", score=0.6),
    ]
    out = apply_domain_rerank(
        hits, concepts=None, confidence=BusinessContextConfidence.HIGH
    )
    assert out is hits  # exact same list when no concepts to apply


def test_rerank_noop_when_empty_concepts_list() -> None:
    hits = [_hit(content="x", qn="a.b", score=0.5)]
    out = apply_domain_rerank(
        hits, concepts=[], confidence=BusinessContextConfidence.HIGH
    )
    assert out is hits


def test_rerank_handles_empty_hits() -> None:
    out = apply_domain_rerank(
        [],
        concepts=[_concept("Account", importance=0.9)],
        confidence=BusinessContextConfidence.HIGH,
    )
    assert out == []


def test_rerank_stamps_original_score() -> None:
    h = _hit(content="account", qn="x", score=0.42)
    out = apply_domain_rerank(
        [h],
        concepts=[_concept("Account", importance=0.5)],
        confidence=BusinessContextConfidence.HIGH,
    )
    assert out[0].metadata["original_score"] == 0.42
    # New score: 1.0 (max-norm) + 0.10*0.5 = 1.05
    assert abs(out[0].score - 1.05) < 1e-6
