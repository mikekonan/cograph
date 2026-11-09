"""Tests for the H7 `too_early_giveup_rate` scoring rule in cograph-eval.

The rule's whole reason to exist is to flag the "agent gave up after one
empty retrieve" failure. We cover four shapes that matter:

  * positive question + give-up phrase + <2 calls -> H7 fires
  * positive question + give-up phrase + ≥2 calls -> H7 quiet (the
    agent earned the give-up)
  * negative question + give-up phrase + <2 calls -> H7 quiet (the
    correct answer for a question with no answer)
  * positive question, no give-up phrase -> H7 quiet (correct answer)

Also pins the give-up regex against the exact playbook phrase that the
operator-facing instructions emit (`backend/app/mcp/instructions.py`),
so a future refactor that softens the playbook wording without
updating the eval pattern is caught here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# eval/ is a sibling of backend/ — point pytest at it explicitly so the
# `eval.cograph_mcp_eval.score` import resolves without an editable
# install for the eval package. (pyproject.toml only sets pythonpath to
# `..`, which gives us the backend root — `eval` lives next door.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.cograph_mcp_eval.record import _validate  # noqa: E402
from eval.cograph_mcp_eval.score import (  # noqa: E402
    _aggregate,
    _is_giveup_phrase,
    _score_record,
)


def _positive_question() -> dict:
    return {
        "category": "feature_flow",
        "expected_answer_keywords": ["jwt"],
        "expected_provenance": [],
        "negative": False,
    }


def _negative_question() -> dict:
    return {
        "category": "negative_doesnt_exist",
        "expected_answer_keywords": ["not found", "no such", "doesn't exist"],
        "expected_provenance": [],
        "negative": True,
    }


def _record(
    *,
    answer: str = "stub",
    tool_calls_count: int = 0,
    answer_chars: int | None = None,
    result_bytes_total: int = 0,
    citations_seen: list[str] | None = None,
    qid: str = "q1",
) -> dict:
    return {
        "id": qid,
        "answer": answer,
        "tool_calls_count": tool_calls_count,
        "answer_chars": answer_chars
        if answer_chars is not None
        else len(answer),
        "result_bytes_total": result_bytes_total,
        "citations_seen": citations_seen or [],
    }


# ---------- _is_giveup_phrase ------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "I don't have enough information in this Cograph instance to answer.",
        "I dont have enough information.",
        "I do not have enough information in this Cograph instance to answer.",
        "Insufficient information to answer this.",
        "I can't answer that — no matching code or wiki found.",
        "No information about that feature is indexed here.",
        # Plain refusals without the playbook phrase.
        "I could not find a FactoryOfFactoryFactory class in samber/lo.",
        "Search found no symbol with that name.",
        "No matches for that query.",
        "That repository is not indexed in this instance.",
    ],
)
def test_is_giveup_phrase_matches_expected_wordings(answer: str) -> None:
    # Each of these is a wording the agent might actually emit under the
    # playbook's "say exactly: I don't have enough information…" rule.
    # Loose alternation in the regex lets every shape through.
    assert _is_giveup_phrase(answer), answer


@pytest.mark.parametrize(
    "answer",
    [
        "Authentication flows through JWT and CSRF cookies; see auth.py:42-90.",
        "The repo provides idempotency middleware (see middleware/idempotency.py).",
        "",
    ],
)
def test_is_giveup_phrase_ignores_real_answers(answer: str) -> None:
    assert not _is_giveup_phrase(answer), answer


# ---------- _score_record: H7 ------------------------------------------------


def test_h7_fires_when_agent_gives_up_after_one_call() -> None:
    record = _record(
        answer="I don't have enough information to answer.",
        tool_calls_count=1,
    )
    scored = _score_record(record, _positive_question())
    assert scored["too_early_giveup"] is True


def test_h7_quiet_when_agent_earned_the_giveup() -> None:
    # Two different attempts came back empty: that is enough to stop.
    record = _record(
        answer="I don't have enough information in this Cograph instance to answer.",
        tool_calls_count=2,
    )
    scored = _score_record(record, _positive_question())
    assert scored["too_early_giveup"] is False


def test_h7_quiet_on_negative_questions() -> None:
    # The question asks about something that genuinely doesn't exist;
    # giving up after one tool call is the right answer there.
    record = _record(
        answer="I don't have enough information about that feature.",
        tool_calls_count=1,
    )
    scored = _score_record(record, _negative_question())
    assert scored["too_early_giveup"] is False


def test_h7_quiet_on_substantive_short_answers() -> None:
    # Few calls, but the agent actually answered — not a giveup.
    record = _record(
        answer="Auth uses JWT + CSRF cookies; see auth.py:42-90.",
        tool_calls_count=2,
    )
    scored = _score_record(record, _positive_question())
    assert scored["too_early_giveup"] is False


# ---------- _aggregate: H7 rate ---------------------------------------------


def test_h7_rate_is_proportional_across_population() -> None:
    # Hand-build a small population: 2 of 4 records hit H7. Rate must
    # land at 0.5 exactly.
    pos = _positive_question()
    scored = [
        _score_record(
            _record(
                qid="q1",
                answer="I don't have enough information.",
                tool_calls_count=1,
            ),
            pos,
        ),
        _score_record(
            _record(
                qid="q2",
                answer="I could not find it.",
                tool_calls_count=0,
            ),
            pos,
        ),
        _score_record(
            _record(
                qid="q3",
                answer="JWT + CSRF cookies, see auth.py.",
                tool_calls_count=3,
            ),
            pos,
        ),
        _score_record(
            _record(
                qid="q4",
                answer="I don't have enough information.",
                tool_calls_count=5,
            ),
            pos,
        ),
    ]
    summary = _aggregate(scored)
    assert summary["too_early_giveup_rate"] == 0.5, summary


def test_h7_rate_is_zero_on_a_clean_run() -> None:
    pos = _positive_question()
    scored = [
        _score_record(
            _record(
                qid=f"q{i}",
                answer="Real answer with cite auth.py:42-90.",
                tool_calls_count=3,
            ),
            pos,
        )
        for i in range(3)
    ]
    summary = _aggregate(scored)
    assert summary["too_early_giveup_rate"] == 0.0
    # Reading local files is not penalised: cost (H1/H2) covers it.
    assert "silent_fallback_rate" not in summary


# ---------- negative questions ----------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "No such class in samber/lo.",  # one keyword out of three
        "I looked in samber/lo and could not find that class.",  # free wording
    ],
)
def test_negative_question_is_correct_on_any_refusal(answer: str) -> None:
    scored = _score_record(_record(answer=answer, tool_calls_count=1), _negative_question())
    assert scored["correctness"] == "correct"


def test_negative_question_is_incorrect_on_a_made_up_answer() -> None:
    record = _record(answer="It lives in factory.go:12-40.", tool_calls_count=1)
    scored = _score_record(record, _negative_question())
    assert scored["correctness"] == "incorrect"


# ---------- cost ------------------------------------------------------------


def test_cost_counts_client_tool_calls() -> None:
    record = _validate(
        {
            "tool_calls": [
                {"name": "mcp__cograph__cograph_retrieve", "result_bytes": 900},
                {"name": "Grep", "result_bytes": 100},
            ],
            "answer": "stub",
        },
        "q1",
    )
    assert record["tool_calls_count"] == 2
    assert record["result_bytes_total"] == 1000


def test_playbook_text_matches_eval_giveup_pattern() -> None:
    # Tighten the loop: the playbook bakes a specific give-up phrase
    # into `backend/app/mcp/instructions.py`. If a future PR softens the
    # wording in the playbook without updating the eval regex, the
    # metric stops counting real failures. This pins both sides
    # together.
    from backend.app.mcp.instructions import render_instructions
    from backend.app.config import get_settings

    rendered = render_instructions(None, settings=get_settings())
    # Find the canonical phrase the playbook tells the agent to use.
    # We don't pin the wrap (the playbook may reflow), so anchor on the
    # part the eval regex looks for and verify the rendered text
    # contains it modulo whitespace.
    import re

    assert re.search(
        r"I don'?t have enough information in this\s+Cograph instance to\s+answer",
        rendered,
    ), rendered
    # And the same phrase, normalised, hits the eval pattern.
    normalised = re.sub(r"\s+", " ", rendered)
    assert _is_giveup_phrase(normalised), normalised
