from mcp.server.fastmcp import FastMCP

from backend.app.mcp.services import MCPServices
from backend.app.mcp.tools.collections import register as register_collection_tools
from backend.app.mcp.tools.node import register as register_node_tool
from backend.app.mcp.tools.related import register as register_related_tool
from backend.app.mcp.tools.repositories import register as register_repositories_tool
from backend.app.mcp.tools.retrieve import register as register_retrieve_tool
from backend.app.mcp.tools.search import register as register_search_tool
from backend.app.mcp.tools.search_code import register as register_search_code_tool


def register_tools(server: FastMCP, services: MCPServices) -> None:
    register_repositories_tool(server, services)
    register_collection_tools(server, services)
    register_retrieve_tool(server, services)
    register_node_tool(server, services)
    register_search_tool(server, services)
    register_search_code_tool(server, services)
    register_related_tool(server, services)
