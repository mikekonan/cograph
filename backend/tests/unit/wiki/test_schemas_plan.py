"""Tolerance tests for `PageSpec.covers_questions` parsing.

`ReaderQuestion` is a closed five-value contract. The planner LLM
occasionally invents a sixth slug (observed in prod: `operational-concerns`
on merchant-registry, which failed the whole repo's wiki stage after the
two `plan_pages` retries). The `_drop_unknown_questions` validator must
absorb that hallucination instead of rejecting the entire `PagePlan`.
"""

from __future__ import annotations

from backend.app.wiki.schemas import PagePlan, PageSpec, ReaderQuestion


def test_unknown_covers_question_is_dropped_not_fatal():
    """The exact prod failure: a hallucinated slug on one page.

    Before the fix, `model_validate_json` raised a `ValidationError` for
    the unknown enum value and `plan_pages` died. Now the unknown slug is
    silently dropped and the valid ones survive.
    """
    raw = (
        '{"pages": ['
        '{"slug": "index", "title": "Merchant Registry Service", "purpose": "x",'
        ' "covers_questions": ["use-cases", "how-to-run", "public-api",'
        ' "dependencies", "configuration"]},'
        '{"slug": "operations", "title": "Operations", "purpose": "y",'
        ' "covers_questions": ["how-to-run", "operational-concerns", "configuration"]}'
        "]}"
    )

    plan = PagePlan.model_validate_json(raw)

    assert len(plan.pages) == 2
    assert plan.pages[0].covers_questions == [
        ReaderQuestion.USE_CASES,
        ReaderQuestion.HOW_TO_RUN,
        ReaderQuestion.PUBLIC_API,
        ReaderQuestion.DEPENDENCIES,
        ReaderQuestion.CONFIGURATION,
    ]
    # The bad slug is gone; the two valid ones around it are kept in order.
    assert plan.pages[1].covers_questions == [
        ReaderQuestion.HOW_TO_RUN,
        ReaderQuestion.CONFIGURATION,
    ]


def test_valid_questions_untouched_and_deduped():
    """Valid slugs pass through; duplicates collapse, order preserved."""
    spec = PageSpec.model_validate(
        {
            "slug": "x",
            "title": "X",
            "purpose": "p",
            "covers_questions": [
                "configuration",
                "made-up",
                "configuration",
                "how-to-run",
            ],
        }
    )
    assert spec.covers_questions == [
        ReaderQuestion.CONFIGURATION,
        ReaderQuestion.HOW_TO_RUN,
    ]


def test_enum_members_pass_through():
    """Code paths that build a PageSpec with real enum members are unaffected."""
    spec = PageSpec(
        slug="x",
        title="X",
        purpose="p",
        covers_questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.USE_CASES],
    )
    assert spec.covers_questions == [
        ReaderQuestion.PUBLIC_API,
        ReaderQuestion.USE_CASES,
    ]


def test_all_questions_unknown_yields_empty_list():
    """Every slug hallucinated → empty coverage, still a valid PageSpec."""
    spec = PageSpec.model_validate(
        {
            "slug": "x",
            "title": "X",
            "purpose": "p",
            "covers_questions": ["operational-concerns", "monitoring"],
        }
    )
    assert spec.covers_questions == []
