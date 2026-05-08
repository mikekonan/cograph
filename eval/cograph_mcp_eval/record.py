"""Append a per-question eval record to results/<target>.jsonl.

Invoked from inside the eval-skill loop (Claude calls this once per
question). The agent (Claude) is responsible for collecting tool calls
and the final answer; this module just persists.

Usage:
    python -m eval.cograph_mcp_eval.record \\
        --target=baseline \\
        --question-id=repo-overview-cograph \\
        --record-json='{"tool_calls": [...], "answer": "...", ...}'

Or pipe JSON on stdin:
    cat record.json | python -m eval.cograph_mcp_eval.record \\
        --target=baseline --question-id=repo-overview-cograph
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_FIELDS = {"tool_calls", "answer"}
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def _validate(record: dict, question_id: str) -> dict:
    missing = REQUIRED_FIELDS - record.keys()
    if missing:
        raise SystemExit(f"record missing required fields: {sorted(missing)}")

    record["id"] = question_id
    record["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tool_calls = record["tool_calls"]
    if not isinstance(tool_calls, list):
        raise SystemExit("tool_calls must be a list")

    record.setdefault("tool_calls_count", len(tool_calls))
    record.setdefault(
        "result_bytes_total",
        sum(int(tc.get("result_bytes", 0)) for tc in tool_calls),
    )
    record.setdefault("answer_chars", len(record["answer"]))
    record.setdefault("citations_seen", [])
    return record


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, help="baseline | after | <freeform>")
    p.add_argument("--question-id", required=True)
    p.add_argument(
        "--record-json",
        help="Inline JSON record. If omitted, JSON is read from stdin.",
    )
    args = p.parse_args()

    raw = args.record_json if args.record_json else sys.stdin.read()
    record = _validate(json.loads(raw), args.question_id)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.target}.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"appended {args.question_id} -> {out}")


if __name__ == "__main__":
    main()
