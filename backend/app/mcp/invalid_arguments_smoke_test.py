import asyncio
import json

from mcp import Client
from mcp.server import MCPServer

from app.mcp.adapter import create_langchain_tool_from_mcp


invalid_args_mcp = MCPServer(
    "MCP Invalid Arguments Test Server"
)


@invalid_args_mcp.tool()
def double_number(value: int) -> int:
    """Double an integer value."""

    return value * 2


async def main() -> None:
    # Discover the tool.
    async with Client(invalid_args_mcp) as client:
        tools_result = await client.list_tools()

        mcp_tool = next(
            tool
            for tool in tools_result.tools
            if tool.name == "double_number"
        )

        print("MCP input schema:")
        print(mcp_tool.input_schema)

        # Test MCP directly first.
        print("\n1. Direct MCP invalid argument test")

        direct_result = await client.call_tool(
            "double_number",
            {
                "value": "not-an-integer",
            },
        )

        print("is_error:", direct_result.is_error)
        print("content:", direct_result.content)

    assert direct_result.is_error is True

    # Convert MCP Tool -> LangChain Tool.
    langchain_tool = create_langchain_tool_from_mcp(
        mcp_server=invalid_args_mcp,
        mcp_tool=mcp_tool,
        server_name="invalid-args-test-mcp",
    )

    print("\n2. LangChain Adapter invalid argument test")

    try:
        adapter_result = await langchain_tool.ainvoke(
            {
                "value": "not-an-integer",
            }
        )

        print("Adapter result:")
        print(adapter_result)

        result_data = json.loads(adapter_result)

        assert result_data["status"] == "error"
        assert result_data["success"] is False
        assert result_data["tool_source"] == "mcp"
        assert result_data["tool_name"] == "double_number"
        assert (
            result_data["error_reason"]
            == "invalid_arguments"
        )
        print(
            "\nInvalid arguments were converted "
            "into a structured MCP error."
        )

    except Exception as exc:
        print(
            "\nAdapter invocation raised an exception:"
        )
        print(type(exc).__name__)
        print(str(exc))

        print(
            "\nInvalid arguments were rejected before "
            "the Adapter could return a structured result."
        )


if __name__ == "__main__":
    asyncio.run(main())