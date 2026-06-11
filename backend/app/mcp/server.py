from __future__ import annotations

import argparse

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from backend.app.config import Settings, get_settings
from backend.app.db.session import SessionManager
from backend.app.graph.traversal import GraphTraversalService
from backend.app.llm.runtime_providers import assert_embedding_runtime_configured
from backend.app.mcp.instructions import (
    DEFAULT_BRIEFING,
    get_cached_instructions,
    refresh_cached_instructions,
    render_instructions,
    set_cached_instructions,
)
from backend.app.mcp.resources import register_resources
from backend.app.mcp.services import MCPServices
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


def create_mcp_server(
    *,
    services: MCPServices,
    host: str = "127.0.0.1",
    port: int = 8001,
    streamable_http_path: str = "/mcp",
    sse_path: str = "/sse",
    message_path: str = "/messages/",
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    # Seed with the playbook + default briefing so even a server that boots
    # before the DB is reachable (e.g. during a migration window) still serves
    # a coherent `instructions=`. The first DB-backed refresh happens in the
    # lifespan hook below and again whenever an admin PATCHes the briefing.
    bootstrap_instructions = render_instructions(
        DEFAULT_BRIEFING, settings=services.settings
    )
    set_cached_instructions(bootstrap_instructions)

    server = FastMCP(
        "Cograph",
        instructions=bootstrap_instructions,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        streamable_http_path=streamable_http_path,
        sse_path=sse_path,
        message_path=message_path,
        transport_security=transport_security,
    )

    # FastMCP's underlying `Server.create_initialization_options()` reads
    # `self.instructions` as a plain string each time a new MCP session
    # initialises. We don't subclass Server (FastMCP owns construction) —
    # instead we replace the attribute with a property-like descriptor
    # that returns the freshest cached value. The cache is refreshed:
    #
    #   * once at server boot from the lifespan startup hook,
    #   * after every admin PATCH to /api/admin/mcp/briefing,
    #
    # so a new MCP `initialize` always sees the latest briefing without
    # requiring a process restart.
    _install_dynamic_instructions(server)
    register_tools(server, services)
    register_resources(server, services)
    return server


def _install_dynamic_instructions(server: FastMCP) -> None:
    """Make `_mcp_server.instructions` read from the in-process cache.

    `Server.instructions` is a plain instance attribute. Replacing the
    instance's `__class__` with a thin subclass lets us turn it into a
    read-only property without touching the upstream library. The
    fallback to the stored attribute keeps the server functional if the
    cache somehow gets cleared between requests.
    """

    mcp_server = server._mcp_server
    stored = mcp_server.instructions

    class _DynamicInstructions(type(mcp_server)):  # type: ignore[misc]
        @property
        def instructions(self) -> str | None:  # type: ignore[override]
            cached = get_cached_instructions()
            return cached if cached is not None else stored

        @instructions.setter
        def instructions(self, value: str | None) -> None:
            # Allow upstream code (or our own bootstrap) to seed the value.
            if value is not None:
                set_cached_instructions(value)

    mcp_server.__class__ = _DynamicInstructions


async def refresh_mcp_instructions_from_db(
    session, *, settings: Settings
) -> str:
    """Public entrypoint for the admin PATCH handler.

    Re-renders the playbook + current briefing into the cache so the next
    MCP `initialize` (from any client) sees the new briefing.
    """

    return await refresh_cached_instructions(session, settings=settings)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.app.mcp.server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--mount-path",
        default=None,
        help="Optional mount path override for SSE transport.",
    )
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
        server = create_mcp_server(
            services=services,
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            sse_path="/sse",
        )
        server.run(transport=args.transport, mount_path=args.mount_path)
    finally:
        if owns_session_manager:
            anyio.run(services.session_manager.dispose)


if __name__ == "__main__":
    main()
