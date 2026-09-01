import asyncio

from mcp import Client

from app.mcp.adapter import create_langchain_tool_from_mcp
from app.mcp.server import mcp


async def main() -> None:
    # 1. Discover the MCP tool.
    async with Client(mcp) as client:
        tools_result = await client.list_tools()

    mcp_tool = next(
        tool
        for tool in tools_result.tools
        if tool.name == "get_server_status"
    )

    # 2. Convert MCP Tool -> LangChain StructuredTool.
    langchain_tool = create_langchain_tool_from_mcp(
        mcp_server=mcp,
        mcp_tool=mcp_tool,
        server_name="test-mcp-server",
    )

    print("LangChain tool:")
    print(f"name: {langchain_tool.name}")
    print(f"description: {langchain_tool.description}")
    print(f"args: {langchain_tool.args}")
    print(f"metadata: {langchain_tool.metadata}")

    # 3. Invoke it through the LangChain Tool interface.
    result = await langchain_tool.ainvoke(
        {
            "component": "langgraph-adapter-test",
        }
    )

    print("\nLangChain tool result:")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())