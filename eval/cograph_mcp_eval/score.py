"""Read results/<target>.jsonl, apply scoring rules, emit summary.json.

Scoring rules (see plan §"Hypothesis verification"):

- correctness: "correct" if every expected_answer_keyword is present in
  the answer (case-insensitive); "partial" if >=50%; "incorrect" otherwise.
- cites_provenance: at least one citations_seen entry contains any of
  expected_provenance as substring.
- silent_fallback: tool_calls_count == 0 AND answer_chars > 50 AND
  correctness != "incorrect" AND not negative.
- too_early_giveup (H7): the answer matches an "I don't know" / "not
  enough information" pattern AND tool_calls_count < 3 AND the
  question is NOT marked negative. Captures the "agent gave up after
  one empty retrieve" failure the playbook is explicitly designed to
  prevent.
- tokens_estimate: result_bytes_total // 4 (rough proxy).

Aggregations: median over the population for tool_calls and tokens;
rates (proportions) for correctness, cites_provenance, silent_fallback,
too_early_giveup.

Usage:
    python -m eval.cograph_mcp_eval.score --target=baseline
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

import yaml


# H7 trigger phrases — the playbook tells the agent to end with one of
# these when it has genuinely tried ≥3 distinct approaches. If the agent
# emits the phrase with <3 calls instead, the rule has failed.
# We deliberately use loose alternation (don't / dont, "have" / "know") so
# minor agent variation doesn't slip past — false positives are cheap (the
# eval flags one extra record), false negatives are expensive (the metric
# silently underreports the failure mode it exists to measure).
_GIVEUP_RE = re.compile(
    r"(?i)\b(?:(?:don'?t|do not) (?:have|know)|not enough information|"
    r"insufficient information|cannot answer|can'?t answer|"
    r"no information (?:in|about))",
)

# Minimum distinct attempts the playbook demands before "I don't know"
# is the right answer. See `backend/app/mcp/instructions.py` — the same
# threshold is described in prose there; this constant is the executable
# counterpart.
_MIN_ATTEMPTS_BEFORE_GIVEUP = 3

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"


def _load_questions() -> dict[str, dict]:
    raw = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return {q["id"]: q for q in raw["questions"]}


def _grade_correctness(answer: str, keywords: list[str]) -> str:
    if not keywords:
        return "correct"
    lowered = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in lowered)
    ratio = hits / len(keywords)
    if hits == len(keywords):
        return "correct"
    if ratio >= 0.5:
        return "partial"
    return "incorrect"


def _cites_provenance(citations: list[str], expected: list[str]) -> bool:
    if not expected:
        return True
    return any(
        any(want in cite for cite in citations) for want in expected
    )


def _is_giveup_phrase(answer: str) -> bool:
    return bool(_GIVEUP_RE.search(answer or ""))


def _score_record(record: dict, question: dict) -> dict:
    correctness = _grade_correctness(
        record["answer"], question.get("expected_answer_keywords", [])
    )
    cites = _cites_provenance(
        record.get("citations_seen", []),
        question.get("expected_provenance", []),
    )
    silent = (
        record["tool_calls_count"] == 0
        and record["answer_chars"] > 50
        and correctness != "incorrect"
        and not question.get("negative", False)
    )
    # H7: only applies to positive questions. A `negative: true` question
    # is one where the agent SHOULD say "I don't know" — counting it
    # would invert the signal we're trying to measure.
    too_early_giveup = (
        not question.get("negative", False)
        and record["tool_calls_count"] < _MIN_ATTEMPTS_BEFORE_GIVEUP
        and _is_giveup_phrase(record["answer"])
    )
    tokens = record["result_bytes_total"] // 4
    return {
        "id": record["id"],
        "category": question["category"],
        "correctness": correctness,
        "cites_provenance": cites,
        "silent_fallback": silent,
        "too_early_giveup": too_early_giveup,
        "tool_calls_count": record["tool_calls_count"],
        "tokens_estimate": tokens,
        "answer_chars": record["answer_chars"],
    }


def _aggregate(scored: list[dict]) -> dict:
    if not scored:
        return {"questions": 0}

    n = len(scored)
    correct = sum(1 for s in scored if s["correctness"] == "correct")
    incorrect = sum(1 for s in scored if s["correctness"] == "incorrect")
    cites = sum(1 for s in scored if s["cites_provenance"])
    silent = sum(1 for s in scored if s["silent_fallback"])
    too_early = sum(1 for s in scored if s["too_early_giveup"])
    calls = [s["tool_calls_count"] for s in scored]
    tokens = [s["tokens_estimate"] for s in scored]

    by_cat: dict[str, list[dict]] = {}
    for s in scored:
        by_cat.setdefault(s["category"], []).append(s)

    cat_summary = {
        cat: {
            "n": len(rows),
            "correctness_rate": sum(
                1 for r in rows if r["correctness"] == "correct"
            ) / len(rows),
            "median_tool_calls": statistics.median(
                r["tool_calls_count"] for r in rows
            ),
            "median_tokens_estimate": statistics.median(
                r["tokens_estimate"] for r in rows
            ),
        }
        for cat, rows in by_cat.items()
    }

    return {
        "questions": n,
        "correctness_rate": correct / n,
        "incorrect_rate": incorrect / n,
        "cites_provenance_rate": cites / n,
        "silent_fallback_rate": silent / n,
        "too_early_giveup_rate": too_early / n,
        "median_tool_calls": statistics.median(calls),
        "median_tokens_estimate": statistics.median(tokens),
        "by_category": cat_summary,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, help="baseline | after | <freeform>")
    args = p.parse_args()

    jsonl = RESULTS_DIR / f"{args.target}.jsonl"
    if not jsonl.exists():
        raise SystemExit(f"no jsonl at {jsonl} — run the eval skill first")

    questions = _load_questions()
    scored: list[dict] = []
    seen: set[str] = set()
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        qid = record["id"]
        if qid in seen:
            # Last-write-wins on duplicates so re-runs don't double-count.
            scored = [s for s in scored if s["id"] != qid]
        seen.add(qid)
        if qid not in questions:
            print(f"warn: record {qid} not in questions.yaml — skipping")
            continue
        scored.append(_score_record(record, questions[qid]))

    summary = _aggregate(scored)
    summary["target"] = args.target
    summary["coverage"] = f"{len(scored)} / {len(questions)}"

    out = RESULTS_DIR / f"{args.target}.summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
