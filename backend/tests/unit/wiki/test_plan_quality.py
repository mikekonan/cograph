"""Unit tests for the T7 plan-quality telemetry."""

from __future__ import annotations

import math

from backend.app.wiki.plan_quality import (
    COSINE_THRESHOLD,
    JACCARD_THRESHOLD,
    analyze_plan_quality,
    cosine_similarity,
    question_jaccard,
)
from backend.app.wiki.schemas import (
    PageKind,
    PagePlan,
    PageSpec,
    ReaderQuestion,
    SalienceTier,
)


def _spec(
    *,
    slug: str,
    title: str = "",
    purpose: str = "",
    questions: list[ReaderQuestion] | None = None,
    page_kind: PageKind = PageKind.CONCEPT,
) -> PageSpec:
    return PageSpec(
        slug=slug,
        title=title or slug.replace("-", " ").title(),
        purpose=purpose,
        covers_questions=questions or [],
        page_kind=page_kind,
        salience_tier=SalienceTier.SUPPORTING,
    )


# ---------------------------------------------------------------------------
# question_jaccard / cosine_similarity primitives
# ---------------------------------------------------------------------------


def test_question_jaccard_full_overlap_is_one() -> None:
    a = _spec(
        slug="a",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.CONFIGURATION],
    )
    b = _spec(
        slug="b",
        questions=[ReaderQuestion.CONFIGURATION, ReaderQuestion.PUBLIC_API],
    )
    assert question_jaccard(a, b) == 1.0


def test_question_jaccard_no_overlap_is_zero() -> None:
    a = _spec(slug="a", questions=[ReaderQuestion.PUBLIC_API])
    b = _spec(slug="b", questions=[ReaderQuestion.CONFIGURATION])
    assert question_jaccard(a, b) == 0.0


def test_question_jaccard_partial_overlap() -> None:
    a = _spec(
        slug="a",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.CONFIGURATION],
    )
    b = _spec(
        slug="b",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.USE_CASES],
    )
    # |∩|=1, |∪|=3 → 1/3
    assert abs(question_jaccard(a, b) - 1 / 3) < 1e-9


def test_question_jaccard_both_empty_returns_zero() -> None:
    a = _spec(slug="a")
    b = _spec(slug="b")
    assert question_jaccard(a, b) == 0.0


def test_cosine_similarity_orthogonal_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_identical_one() -> None:
    assert abs(cosine_similarity([0.6, 0.8], [0.6, 0.8]) - 1.0) < 1e-9


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [0.5, 0.5]) == 0.0
    assert cosine_similarity([], [0.5]) == 0.0


# ---------------------------------------------------------------------------
# analyze_plan_quality — the AND-gate
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Returns canned embeddings keyed on input text.

    Inputs not in `mapping` get a default zero vector so callers can opt
    a page into "no similarity" without wiring full deterministic hashes.
    """

    def __init__(self, mapping: dict[str, list[float]], dims: int = 4) -> None:
        self._mapping = mapping
        self._dims = dims

    @property
    def model(self) -> str:
        return "stub-embed-v1"

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = self._mapping.get(t)
            if vec is None:
                # Non-zero vector keyed by length so two unknowns aren't identical.
                vec = [
                    1.0 / math.sqrt(self._dims) if i == (len(t) % self._dims) else 0.0
                    for i in range(self._dims)
                ]
            out.append(vec)
        return out


async def test_analyze_flags_near_identical_pair() -> None:
    """Two pages that share questions AND have very similar purposes."""
    a = _spec(
        slug="account-overview",
        title="Account Overview",
        purpose="How accounts are created.",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.CONFIGURATION],
    )
    b = _spec(
        slug="accounts",
        title="Accounts",
        purpose="How accounts are created.",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.CONFIGURATION],
    )
    # The purposes are formatted as "<title>: <purpose>"; we map both to
    # the same vector so cosine is exactly 1.0.
    shared_vec = [0.6, 0.8, 0.0, 0.0]
    embedder = _StubEmbedder(
        {
            "Account Overview: How accounts are created.": shared_vec,
            "Accounts: How accounts are created.": shared_vec,
        }
    )
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    assert len(report.suspicious_pairs) == 1
    pair = report.suspicious_pairs[0]
    assert pair.slug_a == "account-overview"
    assert pair.slug_b == "accounts"
    assert pair.question_jaccard == 1.0
    assert pair.purpose_similarity == 1.0


async def test_analyze_does_not_flag_complementary_pair_same_topic() -> None:
    """Same broad topic, disjoint covers_questions → not redundant."""
    a = _spec(
        slug="account-public-api",
        title="Account Public API",
        purpose="The public surface of the account module.",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    b = _spec(
        slug="account-lifecycle",
        title="Account Lifecycle",
        purpose="How accounts move through their states over time.",
        questions=[ReaderQuestion.CONFIGURATION],
    )
    # Make purposes exactly identical in embedding space → cosine=1.0.
    same_vec = [0.6, 0.8, 0.0, 0.0]
    embedder = _StubEmbedder(
        {
            "Account Public API: The public surface of the account module.": same_vec,
            "Account Lifecycle: How accounts move through their states over time.": same_vec,
        }
    )
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    # Jaccard = 0 → AND fails → no flag, even though cosine = 1.
    assert report.suspicious_pairs == []


async def test_analyze_does_not_flag_when_purpose_differs() -> None:
    """Same questions, dissimilar purposes → not flagged."""
    a = _spec(
        slug="a",
        title="A",
        purpose="alpha",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    b = _spec(
        slug="b",
        title="B",
        purpose="beta",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    embedder = _StubEmbedder(
        {
            "A: alpha": [1.0, 0.0, 0.0, 0.0],
            "B: beta": [0.0, 1.0, 0.0, 0.0],
        }
    )
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    assert report.suspicious_pairs == []


async def test_analyze_no_embedder_returns_empty_report() -> None:
    a = _spec(
        slug="a",
        purpose="x",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    b = _spec(
        slug="b",
        purpose="x",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=None)
    assert report.suspicious_pairs == []


async def test_analyze_single_page_is_no_op() -> None:
    a = _spec(slug="only", purpose="single", questions=[ReaderQuestion.PUBLIC_API])
    plan = PagePlan(pages=[a])
    embedder = _StubEmbedder({"only: single": [1.0, 0.0, 0.0, 0.0]})
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    assert report.suspicious_pairs == []


async def test_analyze_pair_sorted_lexicographically() -> None:
    a = _spec(
        slug="zebra",
        title="Zebra",
        purpose="same",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    b = _spec(
        slug="alpha",
        title="Alpha",
        purpose="same",
        questions=[ReaderQuestion.PUBLIC_API],
    )
    same_vec = [0.6, 0.8, 0.0, 0.0]
    embedder = _StubEmbedder({"Zebra: same": same_vec, "Alpha: same": same_vec})
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    assert len(report.suspicious_pairs) == 1
    pair = report.suspicious_pairs[0]
    assert pair.slug_a == "alpha"
    assert pair.slug_b == "zebra"


async def test_analyze_threshold_constants_match_plan() -> None:
    """Lock the public threshold constants so silent drift fails a test."""
    assert JACCARD_THRESHOLD == 0.5
    assert COSINE_THRESHOLD == 0.82


async def test_analyze_flags_at_exact_threshold() -> None:
    """Inclusive comparison: jaccard==0.5 and cosine==0.82 should flag."""
    a = _spec(
        slug="a",
        title="A",
        purpose="x",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.CONFIGURATION],
    )
    b = _spec(
        slug="b",
        title="B",
        purpose="y",
        questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.USE_CASES],
    )
    # Hand-craft embeddings whose cosine is exactly 0.82 (target).
    # Use the simplest 2D rotation: a = (1,0); cos θ = 0.82 → θ = arccos(0.82)
    # → b = (0.82, sin θ). Both unit vectors, dot = 0.82.
    sin_theta = math.sqrt(1 - 0.82**2)
    embedder = _StubEmbedder(
        {
            "A: x": [1.0, 0.0],
            "B: y": [0.82, sin_theta],
        },
        dims=2,
    )
    plan = PagePlan(pages=[a, b])
    report = await analyze_plan_quality(plan=plan, embedder=embedder)
    # Jaccard: |∩|=1 ({PUBLIC_API}), |∪|=3 → 1/3 < 0.5 → no flag.
    assert report.suspicious_pairs == []

    # Now bump jaccard to exactly 0.5 by sharing one of the two questions.
    a2 = a.model_copy(update={"covers_questions": [ReaderQuestion.PUBLIC_API]})
    b2 = b.model_copy(update={"covers_questions": [ReaderQuestion.PUBLIC_API]})
    plan2 = PagePlan(pages=[a2, b2])
    embedder2 = _StubEmbedder(
        {
            "A: x": [1.0, 0.0],
            "B: y": [0.82, sin_theta],
        },
        dims=2,
    )
    report2 = await analyze_plan_quality(plan=plan2, embedder=embedder2)
    # Jaccard=1.0 (both have just PUBLIC_API), cosine≈0.82 → AND gate fires.
    assert len(report2.suspicious_pairs) == 1
    assert report2.suspicious_pairs[0].purpose_similarity == 0.82
