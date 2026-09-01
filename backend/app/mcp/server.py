from mcp.server import MCPServer


mcp = MCPServer("LangGraph RAG Agent MCP")


@mcp.tool()
def get_server_status(component: str = "mcp") -> str:
    """Return the status of a test component exposed by the MCP server."""

    return f"{component} is available"