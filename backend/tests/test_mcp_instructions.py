"""The `instructions=` text: short enough that clients show all of it."""

from __future__ import annotations

import re

from mcp import Client

from backend.app.mcp.instructions import (
    BRIEFING_LIMIT,
    INSTRUCTIONS,
    INSTRUCTIONS_LIMIT,
    compose_instructions,
)
from backend.app.mcp.server import build_mcp_services, create_mcp_server
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing


def test_instructions_fit_what_clients_show() -> None:
    # Clients cut long instructions; the first line is often all a tool
    # list shows.
    assert len(compose_instructions("x" * BRIEFING_LIMIT)) <= INSTRUCTIONS_LIMIT
    assert len(INSTRUCTIONS.splitlines()[0]) <= 300


def test_briefing_that_does_not_fit_is_not_sent() -> None:
    assert compose_instructions("x" * (BRIEFING_LIMIT + 1)) == INSTRUCTIONS
    assert compose_instructions("  \n") == INSTRUCTIONS


async def test_server_sends_instructions_and_every_named_tool_exists() -> None:
    services, _ = build_mcp_services()
    async with Client(create_mcp_server(services=services)) as client:
        assert client.instructions == INSTRUCTIONS
        tools = {t.name for t in (await client.list_tools()).tools}
    named = set(re.findall(r"cograph_[a-z_]+", INSTRUCTIONS))
    assert named and named <= tools, named - tools


async def test_instructions_carry_the_saved_briefing(app, db_session) -> None:
    db_session.add(
        McpOperatorBriefing(id=1, content="Warehouse team: ask before a stock move.")
    )
    await db_session.commit()

    server = app.state.mcp_server
    await server.refresh_instructions()
    async with Client(server) as client:
        assert client.instructions is not None
        assert client.instructions.startswith(INSTRUCTIONS)
        assert client.instructions.endswith(
            "## This deployment\nWarehouse team: ask before a stock move.\n"
        )
