import asyncio
import json

from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.mcp.adapter import create_langchain_tool_from_mcp


failure_mcp = MCPServer("MCP Failure Test Server")


@failure_mcp.tool()
def always_fail(reason: str = "forced_failure") -> str:
    """Always fail so MCP tool error handling can be tested."""

    raise ToolError(
        f"Forced MCP tool failure: {reason}"
    )


async def main() -> None:
    # Discover the failing MCP tool.
    async with Client(failure_mcp) as client:
        tools_result = await client.list_tools()

    mcp_tool = next(
        tool
        for tool in tools_result.tools
        if tool.name == "always_fail"
    )

    # Convert MCP Tool -> LangChain Tool
    langchain_tool = create_langchain_tool_from_mcp(
        mcp_server=failure_mcp,
        mcp_tool=mcp_tool,
        server_name="failure-test-mcp",
    )

    print("Invoking failing MCP tool...")

    result = await langchain_tool.ainvoke(
        {
            "reason": "m8.7-a",
        }
    )

    print("\nAdapter result:")
    print(result)

    result_data = json.loads(result)

    assert result_data["status"] == "error"
    assert result_data["success"] is False
    assert result_data["tool_source"] == "mcp"
    assert result_data["server_name"] == "failure-test-mcp"
    assert result_data["tool_name"] == "always_fail"
    assert result_data["error_reason"] == "tool_execution_error"
    assert result_data["result"] is None
    assert result_data["latency_ms"] >= 0
    assert result_data["content"]

    print("\nMCP tool execution error test passed")


if __name__ == "__main__":
    asyncio.run(main())