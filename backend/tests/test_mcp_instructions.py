"""The `instructions=` text: short enough that clients show all of it."""

from __future__ import annotations

import re

from mcp import Client

from backend.app.mcp.instructions import INSTRUCTIONS
from backend.app.mcp.server import build_mcp_services, create_mcp_server


def test_instructions_fit_what_clients_show() -> None:
    # Clients cut long instructions; the first line is often all a tool
    # list shows.
    assert len(INSTRUCTIONS) <= 2000
    assert len(INSTRUCTIONS.splitlines()[0]) <= 250


async def test_server_sends_instructions_and_every_named_tool_exists() -> None:
    services, _ = build_mcp_services()
    async with Client(create_mcp_server(services=services)) as client:
        assert client.instructions == INSTRUCTIONS
        tools = {t.name for t in (await client.list_tools()).tools}
    named = set(re.findall(r"cograph_[a-z_]+", INSTRUCTIONS))
    assert named and named <= tools, named - tools
