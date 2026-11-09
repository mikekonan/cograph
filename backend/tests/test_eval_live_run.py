"""The live run counts what the agent really did, and the proxy swaps only the text."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.cograph_mcp_eval.live_run import parse_stream, report, to_record  # noqa: E402
from eval.cograph_mcp_eval.proxy import apply_tool_overrides, build_proxy  # noqa: E402

QUESTION = {
    "id": "q1",
    "category": "symbol_lookup",
    "expected_answer_keywords": ["fallback"],
    "expected_provenance": ["option.go"],
    "expected_location": {"file": "option.go", "symbol": "OrElse"},
    "negative": False,
}


def _stream(*results: str, answer: str) -> list[str]:
    events = [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "cograph", "status": "connected"}],
        },
        # Some system events carry `message` as plain text.
        {"type": "system", "subtype": "notice", "message": "text"},
    ]
    for i, text in enumerate(results):
        tool = "mcp__cograph__cograph_retrieve" if i == 0 else "Grep"
        events.append({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": {"query": "x"}},
        ]}})  # fmt: skip
        # The CLI sends a tool result either as a string or as text blocks.
        content = text if i == 0 else [{"type": "text", "text": text}]
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": content},
        ]}})  # fmt: skip
    events.append({"type": "result", "result": answer, "total_cost_usd": 0.01})
    return [json.dumps(e) for e in events] + [""]


def test_counts_every_tool_and_finds_the_answer_location() -> None:
    lines = _stream(
        "option.go: func (o Option[T]) OrElse(fallback T)",
        "é",
        answer="Returns fallback (option.go).",
    )
    parsed = parse_stream(lines)
    record = to_record(parsed, QUESTION, variant="old", run=1)

    assert parsed["mcp"] == "connected"
    assert [c["name"] for c in record["tool_calls"]] == [
        "mcp__cograph__cograph_retrieve",
        "Grep",
    ]
    assert (
        record["result_bytes_total"]
        == len("option.go: func (o Option[T]) OrElse(fallback T)") + 2
    )
    assert record["reached"] is True
    assert record["score"]["correctness"] == "correct"
    assert record["score"]["cites_provenance"] is True


def test_file_and_symbol_in_different_results_is_not_reached() -> None:
    lines = _stream("option.go", "OrElse", answer="I could not find it.")
    record = to_record(parse_stream(lines), QUESTION, variant="old", run=1)

    assert record["reached"] is False
    assert record["score"]["correctness"] == "incorrect"


def test_report_has_a_row_per_question_and_variant(tmp_path) -> None:
    for variant, answer in (("old", "no idea"), ("new", "fallback in option.go")):
        lines = _stream("option.go OrElse", answer=answer)
        record = to_record(parse_stream(lines), QUESTION, variant=variant, run=1)
        (tmp_path / f"{variant}.jsonl").write_text(json.dumps(record) + "\n")

    rows = report(tmp_path, ["old", "new"]).splitlines()[2:]
    assert [r.split()[:3] for r in rows] == [["q1", "new", "1/1"], ["q1", "old", "0/1"]]


async def test_proxy_swaps_text_and_passes_calls_through() -> None:
    upstream_server = MCPServer("cograph", instructions="old text")

    @upstream_server.tool(name="cograph_echo", description="old description")
    async def echo(x: str) -> str:
        return "echo " + x

    async with Client(upstream_server) as upstream:
        passthrough = build_proxy(upstream, {})
        async with Client(passthrough) as agent:
            assert agent.instructions == "old text"

        variant = {
            "instructions": "new text",
            "tools": {"cograph_echo": {"description": "new description"}},
        }
        async with Client(build_proxy(upstream, variant)) as agent:
            assert agent.instructions == "new text"
            tools = (await agent.list_tools()).tools
            assert [(t.name, t.description) for t in tools] == [
                ("cograph_echo", "new description")
            ]
            result = await agent.call_tool("cograph_echo", {"x": "hi"})
            assert result.content[0].text == "echo hi"

    # A typo in a tool name would silently test the old text.
    with pytest.raises(ValueError, match="cograph_ech0"):
        apply_tool_overrides(tools, {"cograph_ech0": {"description": "x"}})
