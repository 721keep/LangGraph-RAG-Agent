import asyncio
import json

from mcp import Client
from mcp.server import MCPServer

from app.mcp.adapter import create_langchain_tool_from_mcp


timeout_mcp = MCPServer(
    "MCP Timeout Test Server"
)


@timeout_mcp.tool()
async def slow_operation(
    delay_seconds: float = 1.0,
) -> str:
    """Wait before returning a result."""

    await asyncio.sleep(delay_seconds)

    return "slow operation completed"


async def main() -> None:
    async with Client(timeout_mcp) as client:
        tools_result = await client.list_tools()

    mcp_tool = next(
        tool
        for tool in tools_result.tools
        if tool.name == "slow_operation"
    )

    langchain_tool = create_langchain_tool_from_mcp(
        mcp_server=timeout_mcp,
        mcp_tool=mcp_tool,
        server_name="timeout-test-mcp",
        timeout_seconds=0.10,
    )

    print("Invoking slow MCP tool...")

    result = await langchain_tool.ainvoke(
        {
            "delay_seconds": 1.0,
        }
    )

    print("\nAdapter result:")
    print(result)

    result_data = json.loads(result)

    assert result_data["status"] == "error"
    assert result_data["success"] is False
    assert result_data["tool_source"] == "mcp"
    assert (
        result_data["server_name"]
        == "timeout-test-mcp"
    )
    assert (
        result_data["tool_name"]
        == "slow_operation"
    )
    assert (
        result_data["error_reason"]
        == "tool_timeout"
    )
    assert result_data["result"] is None
    assert result_data["latency_ms"] >= 0

    print("\nMCP timeout test passed")


if __name__ == "__main__":
    asyncio.run(main())