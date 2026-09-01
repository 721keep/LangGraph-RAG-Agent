import asyncio
import json
from types import SimpleNamespace

from app.mcp.adapter import create_langchain_tool_from_mcp


async def main() -> None:
    # Simulate tool metadata that was discovered
    # while the MCP server was healthy.
    mcp_tool = SimpleNamespace(
        name="unavailable_test_tool",
        description=(
            "Test MCP server unavailable handling."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
    )

    # Nothing should be listening on this address.
    unavailable_server = (
        "http://127.0.0.1:65534/mcp"
    )

    langchain_tool = create_langchain_tool_from_mcp(
        mcp_server=unavailable_server,
        mcp_tool=mcp_tool,
        server_name="unavailable-test-mcp",
        timeout_seconds=2.0,
    )

    print("Invoking unavailable MCP server...")

    result = await langchain_tool.ainvoke({})

    print("\nAdapter result:")
    print(result)

    result_data = json.loads(result)

    assert result_data["status"] == "error"
    assert result_data["success"] is False

    assert (
        result_data["tool_source"]
        == "mcp"
    )

    assert (
        result_data["server_name"]
        == "unavailable-test-mcp"
    )

    assert (
        result_data["tool_name"]
        == "unavailable_test_tool"
    )

    assert (
        result_data["error_reason"]
        == "server_unavailable"
    )

    assert result_data["result"] is None
    assert result_data["latency_ms"] >= 0

    print(
        "\nMCP server unavailable test passed"
    )


if __name__ == "__main__":
    asyncio.run(main())