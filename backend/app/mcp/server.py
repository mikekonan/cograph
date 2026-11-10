from __future__ import annotations

import argparse
from typing import Any

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backend.app.config import Settings, get_settings
from backend.app.db.session import SessionManager
from backend.app.graph.traversal import GraphTraversalService
from backend.app.llm.runtime_providers import assert_embedding_runtime_configured
from backend.app.mcp.instructions import INSTRUCTIONS
from backend.app.mcp.resources import register_resources
from backend.app.mcp.services import MCPServices, mcp_query_log_scope
from backend.app.mcp.tools import register_tools
from backend.app.rag.context_builder import ContextBuilder
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.runtime import build_hybrid_retriever
from backend.app.wiki import WikiQueryService


def build_mcp_services(
    *,
    settings: Settings | None = None,
    session_manager: SessionManager | None = None,
    embed_provider=None,
    retriever=None,
    lexical: LexicalRetriever | None = None,
    symbol: SymbolLookup | None = None,
    context_builder: ContextBuilder | None = None,
    graph_traversal: GraphTraversalService | None = None,
    wiki_queries: WikiQueryService | None = None,
) -> tuple[MCPServices, bool]:
    resolved_settings = settings or get_settings()
    owns_session_manager = session_manager is None
    resolved_session_manager = session_manager or SessionManager(resolved_settings)

    services = MCPServices(
        settings=resolved_settings,
        session_manager=resolved_session_manager,
        embed_provider=embed_provider,
        retriever=retriever
        if retriever is not None
        else build_hybrid_retriever(resolved_settings),
        lexical=lexical or LexicalRetriever(),
        symbol=symbol or SymbolLookup(),
        context_builder=context_builder or ContextBuilder(),
        graph_traversal=graph_traversal or GraphTraversalService(),
        wiki_queries=wiki_queries or WikiQueryService(),
    )
    return services, owns_session_manager


# Names agents reach for out of habit, mapped to the parameter that means the
# same thing on our tools. Applied only where the tool has the target and no
# parameter of the alias's name, so `cograph_repositories` keeps its own `limit`.
# Never published in an inputSchema: this forgives a call, it does not invite one.
_ARGUMENT_ALIASES = {"limit": "top_k", "slug": "repository"}


def _canonical_arguments(
    tool: str, valid: list[str], arguments: dict[str, Any]
) -> dict[str, Any]:
    """Rename aliases to their parameter and reject every other unknown name.

    The SDK validates arguments against a model that ignores extra keys, so an
    unknown name used to vanish: `limit=5` on a `top_k` tool ran with the default.
    """

    for alias, target in _ARGUMENT_ALIASES.items():
        if alias in arguments and alias not in valid and target in valid:
            if target in arguments:
                raise ToolError(
                    f"INVALID_REQUEST: {alias} is another name for {target}; "
                    "pass one of them, not both"
                )
            arguments = {
                target if key == alias else key: value
                for key, value in arguments.items()
            }
    unknown = [key for key in arguments if key not in valid]
    if unknown:
        raise ToolError(
            f"INVALID_REQUEST: {tool} does not take {', '.join(unknown)}. "
            f"Valid arguments: {', '.join(valid)}"
        )
    return arguments


# These write their own query_log row, with result count and token usage.
_SELF_LOGGED_TOOLS = frozenset(
    {"cograph_retrieve", "cograph_search_code", "cograph_collection_search"}
)


class _CographMCPServer(MCPServer):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> Any:
        if name in _SELF_LOGGED_TOOLS:
            return await self._call_tool(name, arguments, context)
        # One row per call for every other tool, so the log shows which
        # tools agents use, not only the searches.
        async with mcp_query_log_scope(
            ctx=context,
            tool_name=name[:64],
            query_text=" ".join(f"{key}={value}" for key, value in arguments.items()),
        ):
            return await self._call_tool(name, arguments, context)

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None,
    ) -> Any:
        # Every transport routes `tools/call` through here, before the SDK
        # validates and drops what it does not know.
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            valid = list(tool.parameters.get("properties") or {})
            arguments = _canonical_arguments(name, valid, arguments)
        return await super().call_tool(name, arguments, context)


def create_mcp_server(*, services: MCPServices) -> MCPServer:
    # Transport configuration — host, port, paths, statelessness and the
    # DNS-rebinding settings — belongs to `streamable_http_app()`, `sse_app()`
    # and `run()` rather than to the constructor, so each caller states it
    # where it actually serves: `main.py` for the mounted app, `main()` below
    # for the standalone process.
    server = _CographMCPServer("Cograph", instructions=INSTRUCTIONS)
    register_tools(server, services)
    register_resources(server, services)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.app.mcp.server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    services, owns_session_manager = build_mcp_services()

    try:

        async def _validate_runtime() -> None:
            async with services.session_manager.session() as session:
                await assert_embedding_runtime_configured(
                    session=session,
                    settings=services.settings,
                )

        anyio.run(_validate_runtime)
        server = create_mcp_server(services=services)
        # Each transport accepts a different keyword set and rejects the
        # others, so build exactly the one it takes. stdio takes none.
        transport_kwargs: dict[str, object] = (
            {} if args.transport == "stdio" else {"host": args.host, "port": args.port}
        )
        if args.transport == "streamable-http":
            transport_kwargs |= {
                "streamable_http_path": "/mcp",
                "stateless_http": True,
                "json_response": True,
            }
        server.run(transport=args.transport, **transport_kwargs)  # type: ignore[arg-type]
    finally:
        if owns_session_manager:
            anyio.run(services.session_manager.dispose)


if __name__ == "__main__":
    main()
