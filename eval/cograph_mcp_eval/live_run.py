"""Run a real agent over a question set and compare text variants per question.

One fresh `claude -p` per question, run and variant. Calls and bytes come from
the agent's event stream, not from the agent's own report. The agent reaches
the server through proxy.py, so a variant swaps the instructions and tool
descriptions without rebuilding the server.

    COGRAPH_MCP_URL=http://localhost:8000/mcp COGRAPH_MCP_TOKEN=... \\
    python -m eval.cograph_mcp_eval.live_run \\
        --variant old --variant new=path/to/new.yaml --runs 3 --out /some/dir

A variant without a file keeps the server's own text. Each variant writes
<out>/<variant>.jsonl, then a per-question table is printed; `--report` prints
it again from those files. Keep a private question set and its results out of
this repository.
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from eval.cograph_mcp_eval.record import _validate
from eval.cograph_mcp_eval.score import _score_record

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "live_questions.yaml"
MCP_PREFIX = "mcp__cograph__"
# Built-in tools the agent can see, and which of them run without a prompt.
# Read-only on purpose: the agent works inside a real checkout.
TOOLS = "Read,Grep,Glob,Bash"
ALLOWED = (
    "mcp__cograph,Read,Grep,Glob,Bash(grep:*),Bash(rg:*),Bash(git grep:*),Bash(ls:*)"
)


def parse_stream(lines) -> dict:
    """Tool calls with their result text, and the final answer, from stream-json."""
    calls: dict[str, dict] = {}
    answer, cost, mcp = "", None, None
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        if event["type"] == "system" and event.get("subtype") == "init":
            mcp = {s["name"]: s["status"] for s in event.get("mcp_servers", [])}.get(
                "cograph"
            )
            continue
        # Other system events carry `message` as plain text.
        content = (
            event["message"]["content"]
            if event["type"] in ("assistant", "user")
            else None
        )
        if event["type"] == "assistant":
            for block in content:
                if block["type"] == "tool_use":
                    calls[block["id"]] = {
                        "name": block["name"],
                        "args": block["input"],
                        "result": "",
                    }
        elif event["type"] == "user" and isinstance(content, list):
            for block in content:
                if block["type"] == "tool_result" and block["tool_use_id"] in calls:
                    calls[block["tool_use_id"]]["result"] = _text(block.get("content"))
        elif event["type"] == "result":
            answer, cost = event.get("result") or "", event.get("total_cost_usd")
    return {
        "tool_calls": list(calls.values()),
        "answer": answer,
        "cost_usd": cost,
        "mcp": mcp,
    }


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [])


def reached(calls: list[dict], location: dict | None) -> bool | None:
    """Did one tool result hold the answer's file and symbol?"""
    if not location:
        return None
    return any(
        location["file"] in c["result"] and location["symbol"] in c["result"]
        for c in calls
    )


def to_record(parsed: dict, question: dict, *, variant: str, run: int) -> dict:
    calls = parsed["tool_calls"]
    record = {
        "tool_calls": [
            {
                "name": c["name"],
                "args": c["args"],
                "result_bytes": len(c["result"].encode()),
            }
            for c in calls
        ],
        "answer": parsed["answer"],
        # The scorer looks for the expected file among the answer's citations.
        "citations_seen": [parsed["answer"]],
        "cost_usd": parsed["cost_usd"],
        "reached": reached(calls, question.get("expected_location")),
        "variant": variant,
        "run": run,
    }
    record = _validate(record, question["id"])
    record["score"] = _score_record(record, question)
    return record


def ask(question: dict, variant_file: Path | None, cwd: Path, args) -> list[str]:
    proxy = [sys.executable, str(HERE / "proxy.py")]
    if variant_file:
        proxy += ["--variant", str(variant_file)]
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "mcp.json"
        # The proxy inherits COGRAPH_MCP_URL and COGRAPH_MCP_TOKEN: no token on disk.
        config.write_text(
            json.dumps(
                {"mcpServers": {"cograph": {"command": proxy[0], "args": proxy[1:]}}}
            )
        )
        cmd = [
            args.claude, "-p", question["question"],
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(config), "--strict-mcp-config",
            "--tools", TOOLS, "--allowedTools", ALLOWED, "--permission-mode", "dontAsk",
            # No user or project settings: plugins, hooks and CLAUDE.md skew the run.
            "--setting-sources", "", "--no-session-persistence", *shlex.split(args.claude_args),
        ]  # fmt: skip
        try:
            done = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"  timeout after {args.timeout}s", file=sys.stderr)
            return []
    if done.returncode:
        print(
            f"  claude exited {done.returncode}: {done.stderr.strip()[-500:]}",
            file=sys.stderr,
        )
    return done.stdout.splitlines()


def run(questions_path: Path, variants: dict[str, Path | None], args) -> None:
    questions = yaml.safe_load(questions_path.read_text(encoding="utf-8"))["questions"]
    args.out.mkdir(parents=True, exist_ok=True)
    for variant, variant_file in variants.items():
        out = args.out / f"{variant}.jsonl"
        out.write_text("")
        for question in questions:
            for n in range(1, args.runs + 1):
                print(f"{variant} {question['id']} run {n}", file=sys.stderr)
                with tempfile.TemporaryDirectory() as empty:
                    cwd = (
                        questions_path.parent / question["cwd"]
                        if "cwd" in question
                        else Path(empty)
                    )
                    lines = ask(question, variant_file, cwd, args)
                parsed = parse_stream(lines)
                # Without the server every answer is noise: stop instead of scoring it.
                if lines and parsed["mcp"] != "connected":
                    raise SystemExit(
                        f"cograph MCP is {parsed['mcp']}: check the URL, token and variant"
                    )
                record = to_record(parsed, question, variant=variant, run=n)
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def report(out_dir: Path, variants: list[str]) -> str:
    """One row per question and variant, so a change shows where it happened."""
    rows: dict[tuple[str, str], list[dict]] = {}
    for variant in variants:
        for line in (
            (out_dir / f"{variant}.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            record = json.loads(line)
            rows.setdefault((record["id"], variant), []).append(record)

    header = f"{'question':<32} {'variant':<10} correct reached cited calls   KB  tools"
    lines = [header, "-" * len(header)]
    for (qid, variant), records in sorted(rows.items()):
        n = len(records)
        scores = [r["score"] for r in records]
        correct = sum(s["correctness"] == "correct" for s in scores)
        cited = sum(s["cites_provenance"] for s in scores)
        got = [r["reached"] for r in records if r["reached"] is not None]
        tools = Counter(
            c["name"].removeprefix(MCP_PREFIX) for r in records for c in r["tool_calls"]
        )
        lines.append(
            f"{qid:<32} {variant:<10} {correct}/{n:<5} "
            f"{f'{sum(got)}/{n}' if got else '-':<7} {cited}/{n:<3} "
            f"{statistics.median(r['tool_calls_count'] for r in records):>5} "
            f"{statistics.median(r['result_bytes_total'] for r in records) / 1024:>4.0f}  "
            + ", ".join(f"{name} {count}" for name, count in tools.most_common())
        )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--questions", type=Path, default=QUESTIONS)
    p.add_argument(
        "--variant", action="append", required=True, help="NAME or NAME=variant.yaml"
    )
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=600, help="seconds per agent run")
    p.add_argument("--claude", default="claude")
    p.add_argument(
        "--claude-args",
        default="",
        help="extra flags, e.g. '--model sonnet'",
    )
    p.add_argument(
        "--report", action="store_true", help="only print the table from --out"
    )
    args = p.parse_args()

    variants = {}
    for spec in args.variant:
        name, _, path = spec.partition("=")
        variants[name] = Path(path).resolve() if path else None
    if not args.report:
        run(args.questions.resolve(), variants, args)
    print(report(args.out, list(variants)))


if __name__ == "__main__":
    main()
