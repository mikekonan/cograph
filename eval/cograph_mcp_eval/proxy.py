"""Stdio MCP proxy that swaps the server's text without rebuilding the server.

The agent sees the instructions and tool descriptions of a variant file;
every call still goes to the real server. Without a variant it passes the
server's own text through, which is the baseline.

Variant file (YAML), both keys optional:

    instructions: |
      New server instructions.
    tools:
      cograph_retrieve:
        description: New description.

Usage (normally started by live_run.py, not by hand):

    COGRAPH_MCP_URL=http://localhost:8000/mcp COGRAPH_MCP_TOKEN=... \\
        python -m eval.cograph_mcp_eval.proxy [--variant FILE]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import anyio
import yaml
from mcp import Client, types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def load_variant(path: str | None) -> dict:
    if not path:
        return {}
    variant = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    unknown = variant.keys() - {"instructions", "tools"}
    if unknown:
        raise SystemExit(f"{path}: unknown keys {sorted(unknown)}")
    return variant


def apply_tool_overrides(tools: list[types.Tool], overrides: dict) -> list[types.Tool]:
    # A typo in a tool name would silently test the old text.
    missing = overrides.keys() - {t.name for t in tools}
    if missing:
        raise ValueError(f"variant overrides unknown tools: {sorted(missing)}")
    return [t.model_copy(update=overrides.get(t.name, {})) for t in tools]


def build_proxy(upstream: Client, variant: dict) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        listed = await upstream.list_tools()
        return types.ListToolsResult(
            tools=apply_tool_overrides(listed.tools, variant.get("tools", {}))
        )

    async def call_tool(
        ctx, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await upstream.call_tool(params.name, params.arguments)

    return Server(
        "cograph",
        instructions=variant.get("instructions", upstream.instructions),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve(url: str, token: str, variant: dict) -> None:
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(url, http_client=http)) as upstream:
        # Fail before the agent starts: a bad variant must not test the old text.
        apply_tool_overrides(
            (await upstream.list_tools()).tools, variant.get("tools", {})
        )
        proxy = build_proxy(upstream, variant)
        async with stdio_server() as (read, write):
            await proxy.run(read, write, proxy.create_initialization_options())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", help="YAML with instructions and/or tool overrides")
    args = p.parse_args()
    variant = load_variant(args.variant)
    anyio.run(
        serve, os.environ["COGRAPH_MCP_URL"], os.environ["COGRAPH_MCP_TOKEN"], variant
    )


if __name__ == "__main__":
    main()
