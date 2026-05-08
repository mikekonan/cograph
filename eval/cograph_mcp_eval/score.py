"""Read results/<target>.jsonl, apply scoring rules, emit summary.json.

Scoring rules (see plan §"Hypothesis verification"):

- correctness: "correct" if every expected_answer_keyword is present in
  the answer (case-insensitive); "partial" if >=50%; "incorrect" otherwise.
- cites_provenance: at least one citations_seen entry contains any of
  expected_provenance as substring.
- silent_fallback: tool_calls_count == 0 AND answer_chars > 50 AND
  correctness != "incorrect" AND not negative.
- tokens_estimate: result_bytes_total // 4 (rough proxy).

Aggregations: median over the population for tool_calls and tokens;
rates (proportions) for correctness, cites_provenance, silent_fallback.

Usage:
    python -m eval.cograph_mcp_eval.score --target=baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import yaml

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
    tokens = record["result_bytes_total"] // 4
    return {
        "id": record["id"],
        "category": question["category"],
        "correctness": correctness,
        "cites_provenance": cites,
        "silent_fallback": silent,
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
