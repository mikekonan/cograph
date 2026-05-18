"""Tests for the rendered MCP `instructions=` payload.

The MCP server can't read per-user state inside FastMCP's
`create_initialization_options()` (no request context yet), so this
test suite only covers what the renderer can actually deliver:

- playbook is always English and contains the load-bearing rules,
- briefing falls back to a default that nudges the operator,
- oversized briefing is truncated (so the playbook doesn't get crowded
  out by a runaway operator paste),
- the cache wiring goes through the right code path.

The persistence-rule snapshot test is deliberately whitespace-tolerant
(`\\s+` between tokens) so reflowing a paragraph doesn't fail the
build, but dropping the rule entirely will.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.mcp.instructions import (
    DEFAULT_BRIEFING,
    get_cached_instructions,
    refresh_cached_instructions,
    render_instructions,
)
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing


def _render_with_default_settings(content: str | None) -> str:
    return render_instructions(content, settings=get_settings())


def test_default_briefing_is_used_when_content_is_empty() -> None:
    text = _render_with_default_settings(None)
    # The default briefing must end up inside the rendered payload — agents
    # need cite-or-bust tone-of-voice from the very first session, even on a
    # deployment whose admin hasn't customised yet.
    assert DEFAULT_BRIEFING.strip().splitlines()[0] in text


def test_default_briefing_is_used_when_content_is_blank_whitespace() -> None:
    text = _render_with_default_settings("   \n\t  \n")
    assert "hasn't been customised yet" in text


def test_operator_briefing_overrides_default() -> None:
    text = _render_with_default_settings("Team: payments. Glossary: acquirer = X.")
    assert "Team: payments" in text
    assert "hasn't been customised yet" not in text


def test_playbook_contains_persistence_rule() -> None:
    text = _render_with_default_settings(None)
    # Whitespace-tolerant — the rule is the load-bearing claim, exact line
    # wrapping is not.
    assert re.search(r"at least three distinct\s+approaches", text), text


def test_playbook_contains_no_single_hit_rule() -> None:
    text = _render_with_default_settings(None)
    assert re.search(r"one hit is a lead", text, re.IGNORECASE), text


def test_playbook_contains_route_step_zero() -> None:
    text = _render_with_default_settings(None)
    # The router is mandatory only when no specific repo is named — make
    # sure the playbook reflects that conditional, not an unconditional
    # "always route first".
    assert "cograph.route(query)" in text
    assert "skip step 0" in text or "Skip step 0" in text


def test_playbook_recommends_re_routing_per_concept() -> None:
    # `cograph.route` is cheap; one prompt that mixes two concepts deserves
    # two route calls. The playbook must keep this guidance verbatim so the
    # agent doesn't collapse multi-concept questions to a single candidate
    # set.
    text = _render_with_default_settings(None)
    assert "Re-route per distinct concept" in text
    assert "every distinct domain term" in text
    # And the "when not to re-route" guard rail so we don't burn tokens.
    assert "When NOT to re-route" in text


def test_playbook_mentions_acl_resource() -> None:
    # ACL list moved to the `cograph://my-context` resource; the playbook
    # must tell the agent to fetch it on session start.
    text = _render_with_default_settings(None)
    assert "cograph://my-context" in text


def test_playbook_mentions_briefing_resource() -> None:
    text = _render_with_default_settings(None)
    assert "cograph://briefing" in text


def test_playbook_demands_provenance() -> None:
    text = _render_with_default_settings(None)
    assert "Citations are mandatory" in text
    assert "file_path:start_line-end_line" in text


def test_playbook_has_giveup_phrase() -> None:
    text = _render_with_default_settings(None)
    # When the answer truly isn't available, the agent should fall through
    # to this exact phrase rather than improvising — eval H6 keys on it.
    # Whitespace-tolerant because the phrase wraps across lines in the
    # playbook prose.
    assert re.search(
        r"I don'?t have enough information in this\s+Cograph instance to\s+answer",
        text,
    ), text


def test_briefing_is_truncated_when_oversized() -> None:
    s = get_settings()
    huge = "x" * (s.mcp.briefing_max_length + 500)
    text = render_instructions(huge, settings=s)
    assert "[…briefing truncated…]" in text
    # And the playbook is still intact — we truncate the briefing, not the
    # playbook itself, so the agent never loses the ladder.
    assert "Cograph operator playbook" in text


@pytest.mark.asyncio
async def test_refresh_cached_instructions_reads_db(db_session) -> None:
    db_session.add(McpOperatorBriefing(id=1, content="Custom team payments rules"))
    await db_session.commit()
    # Sanity-check the row materialised so the failure mode if refresh
    # silently sees nothing is "wrong test", not "wrong production code".
    row = (
        await db_session.execute(
            select(McpOperatorBriefing).where(McpOperatorBriefing.id == 1)
        )
    ).scalar_one()
    assert row.content == "Custom team payments rules"

    rendered = await refresh_cached_instructions(
        db_session, settings=get_settings()
    )
    assert "Custom team payments rules" in rendered
    # The cache must hold the same text the function returned — that's
    # what FastMCP's `instructions` property will read on next initialize.
    assert get_cached_instructions() == rendered
