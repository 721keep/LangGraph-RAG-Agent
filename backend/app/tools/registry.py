from langchain_core.tools import BaseTool
from mcp import Client

from app.chat.tools import retrieve_user_documents, tavily
from app.mcp.adapter import create_langchain_tool_from_mcp
from app.mcp.server import mcp


async def get_agent_tools() -> list[BaseTool]:
    """Return native and MCP tools available to the LangGraph agent."""

    tools: list[BaseTool] = [
        retrieve_user_documents,
        tavily,
    ]

    async with Client(mcp) as client:
        tools_result = await client.list_tools()

    mcp_tools = [
        create_langchain_tool_from_mcp(
            mcp_server=mcp,
            mcp_tool=mcp_tool,
            server_name="test-mcp-server",
        )
        for mcp_tool in tools_result.tools
    ]

    return [
        *tools,
        *mcp_tools,
    ]