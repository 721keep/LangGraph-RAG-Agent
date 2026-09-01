import asyncio
import json

from langchain_core.messages import ToolMessage

from app.chat.schemas import ChatStreamResponse


async def empty_stream():
    if False:
        yield None


async def main() -> None:
    response = ChatStreamResponse(empty_stream())

    tool_content = json.dumps(
        {
            "status": "ok",
            "success": True,
            "tool_source": "mcp",
            "server_name": "test-mcp-server",
            "tool_name": "get_server_status",
            "latency_ms": 116.32,
            "error_reason": None,
            "result": {
                "result": "mcp-stream-test is available"
            },
        }
    )

    message = ToolMessage(
        content=tool_content,
        name="get_server_status",
        tool_call_id="test-tool-call",
    )

    chunk = {
        "tools": {
            "messages": [message]
        }
    }

    events = []

    async for item in response._handle_updates_stream(chunk):
        event = json.loads(item)
        events.append(event)
        print(json.dumps(event, indent=2))

    assert len(events) == 1

    event = events[0]

    assert event["type"] == "tool_result"
    assert event["name"] == "get_server_status"
    assert event["tool_source"] == "mcp"
    assert event["server_name"] == "test-mcp-server"
    assert event["status"] == "ok"
    assert event["success"] is True
    assert event["latency_ms"] == 116.32
    assert event["error_reason"] is None

    print("\nMCP stream observability test passed")


if __name__ == "__main__":
    asyncio.run(main())