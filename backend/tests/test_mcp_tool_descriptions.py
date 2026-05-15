"""Lock the L1/L2/L3 tool-description schema in place.

Every `cograph.*` MCP tool MUST follow:

  L1: a one-line summary (first sentence, ends with a period).
  L2: "Use when: …" — concrete signal that *this* is the right tool.
  L3: "Do NOT use …" — pointer to the sibling tool the agent should
      have used instead.

The agent picks tools by reading descriptions in isolation. A drifted or
missing L2/L3 line means the agent picks the wrong tool in confusing
overlap zones (e.g. cograph.retrieve vs cograph.search_code,
cograph.collection_search vs cograph.read_chunk). This test fails loud
the moment a new tool ships without the schema or an existing one
silently loses a section.

If you intentionally remove the schema requirement from a tool, prefer
adding it to `_EXEMPT` here with a comment explaining why, rather than
weakening the assertions.
"""

from __future__ import annotations

import re

import pytest

from backend.app.mcp.server import build_mcp_services, create_mcp_server


_EXEMPT: frozenset[str] = frozenset()  # no exemptions today

# A line that begins with `Use when:` (allowing leading whitespace) or
# `Do NOT use` is treated as the L2 / L3 marker. We don't pin the exact
# wording beyond that prefix — the body changes as the tool evolves.
_L2_RE = re.compile(r"(?mi)^\s*Use when[:\s]")
_L3_RE = re.compile(r"(?mi)^\s*Do NOT use\b")


@pytest.fixture(scope="module")
def all_tool_descriptions() -> dict[str, str]:
    """Build the MCP server once and lift every registered tool's
    description. Returned dict is `{tool_name: description}`."""
    import asyncio

    services, _ = build_mcp_services()
    server = create_mcp_server(services=services)

    async def _collect() -> dict[str, str]:
        return {t.name: (t.description or "") for t in await server.list_tools()}

    return asyncio.run(_collect())


def test_at_least_the_known_tools_are_registered(all_tool_descriptions) -> None:
    # A backstop against a future refactor that drops a tool without
    # noticing — the cost of losing one (e.g. cograph.route) is the agent
    # quietly regressing to single-source retrieval.
    required = {
        "cograph.repositories",
        "cograph.collections",
        "cograph.collection_document",
        "cograph.collection_search",
        "cograph.read_chunk",
        "cograph.route",
        "cograph.retrieve",
        "cograph.read_node",
        "cograph.search_code",
        "cograph.related",
        "cograph.repository_readme",
        "cograph.read_file_range",
        "cograph.outline",
    }
    missing = required - set(all_tool_descriptions)
    assert not missing, f"missing tools: {sorted(missing)}"


def test_every_tool_starts_with_a_one_line_summary(all_tool_descriptions) -> None:
    """L1: the description must lead with prose that ends in a `.` before
    the first newline. A description that opens with a list, code block,
    or 200-char preamble forces the agent to read past noise."""
    for name, desc in all_tool_descriptions.items():
        if name in _EXEMPT:
            continue
        first_line = desc.split("\n", 1)[0].strip()
        assert first_line, f"{name}: description has no first line"
        assert (
            first_line.endswith(".") or first_line.endswith("!")
        ), f"{name}: L1 must end with a period: {first_line!r}"
        # Cap on L1 length keeps it scannable in client UIs that render
        # the description in a tooltip — 300 chars is roughly two visual
        # lines on a typical terminal.
        assert len(first_line) <= 300, (
            f"{name}: L1 too long ({len(first_line)} chars)"
        )


def test_every_tool_has_a_use_when_clause(all_tool_descriptions) -> None:
    for name, desc in all_tool_descriptions.items():
        if name in _EXEMPT:
            continue
        assert _L2_RE.search(desc), (
            f"{name}: description missing 'Use when: …' clause.\n"
            f"Got:\n{desc}"
        )


def test_every_tool_has_a_do_not_use_clause(all_tool_descriptions) -> None:
    for name, desc in all_tool_descriptions.items():
        if name in _EXEMPT:
            continue
        assert _L3_RE.search(desc), (
            f"{name}: description missing 'Do NOT use …' clause.\n"
            f"Got:\n{desc}"
        )


def test_do_not_use_clause_points_to_a_sibling_tool(all_tool_descriptions) -> None:
    """L3 has one job: redirect the agent to the right sibling. A 'Do NOT
    use' line that doesn't name another `cograph.*` tool is a missed
    redirect — the agent reads it as a vague warning and ignores it."""
    for name, desc in all_tool_descriptions.items():
        if name in _EXEMPT:
            continue
        match = _L3_RE.search(desc)
        if match is None:
            continue  # already caught by the prior test
        # Look at the rest of the description from the L3 line onward.
        l3_tail = desc[match.start():]
        cites = re.findall(r"cograph\.[a-z_]+", l3_tail)
        # The tool may legitimately cite itself in the L3 (e.g. "do not
        # use cograph.X for Y") but it MUST cite at least one sibling.
        siblings = [c for c in cites if c != name]
        assert siblings, (
            f"{name}: 'Do NOT use' clause cites no sibling tool — agents "
            f"need an explicit redirect or they'll just ignore the warning.\n"
            f"L3 tail:\n{l3_tail}"
        )
