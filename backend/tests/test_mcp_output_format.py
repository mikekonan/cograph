"""How MCP tool results are shaped for the agent."""

from backend.app.mcp.services import _head_lines, encode_payload


def test_encode_payload_is_compact_and_drops_empty_below_top_level() -> None:
    hit = {
        "snippet": "x",
        "score": None,
        "content_truncated": False,
        "metadata": {"vector_score": None, "candidate_from": []},
        "related_repo_doc_chunks": [],
    }
    # A dict that only held empties goes too; top-level keys stay even when empty.
    assert encode_payload({"results": [hit], "nodes": {}}) == (
        '{"results":[{"snippet":"x","content_truncated":false}],"nodes":{}}'
    )


def test_read_node_body_is_cut_on_a_line_boundary() -> None:
    body = "def f():\n    return 1\n"
    assert _head_lines(body, 100) == (body, False)
    assert _head_lines(body, 12) == ("def f():", True)


def test_empty_callers_and_callees_survive_compaction() -> None:
    # Graph was requested and the node has no callers: "none" is the answer.
    node = {"name": "f", "callers": [], "callees": [], "summary": None}
    assert encode_payload({"nodes": {"n": node}}) == (
        '{"nodes":{"n":{"name":"f","callers":[],"callees":[]}}}'
    )
