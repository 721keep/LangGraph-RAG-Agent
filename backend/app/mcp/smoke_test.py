import asyncio

from mcp import Client

from app.mcp.server import mcp


async def main() -> None:
    async with Client(mcp) as client:
        tools_result = await client.list_tools()

        print("Discovered tools:")

        for tool in tools_result.tools:
            print(f"- name: {tool.name}")
            print(f"  description: {tool.description}")
            print(f"  input_schema: {tool.input_schema}")

        call_result = await client.call_tool(
            "get_server_status",
            {
                "component": "mcp-client-test",
            },
        )

        print("\nTool call result:")
        print(f"is_error: {call_result.is_error}")
        print(f"content: {call_result.content}")
        print(
            f"structured_content: "
            f"{call_result.structured_content}"
        )


if __name__ == "__main__":
    asyncio.run(main())